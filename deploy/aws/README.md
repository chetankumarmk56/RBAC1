# Deploying to AWS App Runner

The whole POC runs as **one App Runner service**: the [Dockerfile](../../Dockerfile) builds
the frontend with Vite and hands the result to FastAPI, which serves it next to `/api`
from the same origin. That is why there is no CORS configuration and no
`VITE_API_BASE_URL` to set — the browser never talks to a second host.

App Runner does not run databases, so PostgreSQL lives in **Amazon RDS**.

> This guide pushes the image to ECR, which needs two IAM roles you create yourself. If you
> cannot create IAM roles, [apprunner-from-github.md](apprunner-from-github.md) builds the
> same service from the GitHub repository instead, with a managed Python runtime and no
> roles — at the cost of plaintext secrets. Both guides assume the account can still create
> App Runner services at all; AWS closed the service to new customers on April 30, 2026.

```
Browser ──▶ App Runner service (this image)          ──▶ Anthropic / Gemini APIs
              FastAPI  ·  /api/*                          (default public egress)
              static   ·  the built React app
                    │
                    └────────────────────────────────▶ RDS PostgreSQL 16
```

### The networking choice baked into this guide

App Runner's outbound networking is all-or-nothing. Attach a VPC connector and *every*
outbound call leaves through your VPC — including the Anthropic and Gemini calls — which
makes a NAT Gateway mandatory (~$33/month before data charges).

**This guide takes the other path:** default public egress, and an RDS instance marked
publicly accessible. No VPC connector, no NAT. App Runner has no fixed egress IPs, so
the database security group has to allow `0.0.0.0/0` on port 5432 — TLS and the master
password are the only things standing in front of it.

That is a deliberate trade for a short-lived demo. Read [Before you expose
this](#before-you-expose-this) before you leave it running.

---

## 1. Prerequisites

Docker Desktop, and the AWS CLI:

```bash
winget install -e --id Amazon.AWSCLI
```

Reopen the shell afterwards so `aws` is on `PATH`, then sign in with an IAM user or role
that can create RDS, ECR, IAM, Secrets Manager and App Runner resources:

```bash
aws configure
```

Every command below is **Git Bash**, run from the repository root. PowerShell needs
different variable syntax.

## 2. Set the shell variables

Everything downstream reads these. Pick a region App Runner supports, and a strong
database password — it ends up in a connection string reachable from the internet.

```bash
export AWS_REGION=ap-south-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export DB_PASSWORD='<a-long-random-password>'
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/rbac-poc
echo "$ACCOUNT_ID / $AWS_REGION / $ECR_URI"
```

> The ECR repository must sit in the **same region** as the App Runner service.

## 3. Create the database

A security group first, then the instance in the default VPC:

```bash
export VPC_ID=$(aws ec2 describe-vpcs --region $AWS_REGION \
  --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)

export DB_SG=$(aws ec2 create-security-group --region $AWS_REGION \
  --group-name rbac-poc-db-sg \
  --description "RBAC POC PostgreSQL" \
  --vpc-id $VPC_ID --query GroupId --output text)

aws ec2 authorize-security-group-ingress --region $AWS_REGION \
  --group-id $DB_SG --protocol tcp --port 5432 --cidr 0.0.0.0/0
```

```bash
aws rds create-db-instance --region $AWS_REGION \
  --db-instance-identifier rbac-poc-db \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --master-username rbac \
  --master-user-password "$DB_PASSWORD" \
  --db-name rbac_poc \
  --allocated-storage 20 \
  --storage-type gp3 \
  --vpc-security-group-ids $DB_SG \
  --publicly-accessible \
  --backup-retention-period 1 \
  --no-multi-az
```

Creation takes five to ten minutes. Wait for it, then capture the endpoint:

```bash
aws rds wait db-instance-available --region $AWS_REGION --db-instance-identifier rbac-poc-db

export DB_HOST=$(aws rds describe-db-instances --region $AWS_REGION \
  --db-instance-identifier rbac-poc-db \
  --query 'DBInstances[0].Endpoint.Address' --output text)
echo $DB_HOST
```

Nothing here creates the schema — the container does that on first boot.

## 4. Build and push the image

```bash
aws ecr create-repository --region $AWS_REGION --repository-name rbac-poc

aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build --platform linux/amd64 --provenance=false -t rbac-poc:latest .
docker tag rbac-poc:latest $ECR_URI:latest
docker push $ECR_URI:latest
```

> Both build flags matter. App Runner runs x86_64 only, and `--provenance=false` keeps
> Docker from pushing a multi-manifest index that App Runner will not pull.

## 5. Store the secrets

Three values the image should never carry: the connection string, the Anthropic key, and
the JWT signing secret.

```bash
aws secretsmanager create-secret --region $AWS_REGION --name rbac-poc/DATABASE_URL \
  --secret-string "postgresql://rbac:$DB_PASSWORD@$DB_HOST:5432/rbac_poc?sslmode=require"

aws secretsmanager create-secret --region $AWS_REGION --name rbac-poc/ANTHROPIC_API_KEY \
  --secret-string "sk-ant-..."

aws secretsmanager create-secret --region $AWS_REGION --name rbac-poc/JWT_SECRET \
  --secret-string "$(openssl rand -hex 32)"
```

`sslmode=require` encrypts the connection to a database that is open to the internet —
do not drop it. [config.py](../../backend/config.py) rewrites the `postgresql://` scheme
to `postgresql+psycopg://` on the way in and leaves the query string alone, so paste the
URL exactly as written.

## 6. Create the instance role

App Runner reads those secrets as the **instance role**, which is separate from the ECR
access role the console creates for you in the next step.

```bash
aws iam create-role --role-name RbacPocAppRunnerInstanceRole \
  --assume-role-policy-document file://deploy/aws/apprunner-tasks-trust.json

cat > deploy/aws/secrets-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT_ID:secret:rbac-poc/*"
  }]
}
JSON

aws iam put-role-policy --role-name RbacPocAppRunnerInstanceRole \
  --policy-name RbacPocReadSecrets \
  --policy-document file://deploy/aws/secrets-policy.json
```

The wildcard covers the six random characters Secrets Manager appends to every ARN.

## 7. Create the App Runner service

The console wizard is worth using here — it creates the ECR access role for you.
**App Runner → Create service**, then:

**Source and deployment**

| Field                 | Value                                    |
| --------------------- | ---------------------------------------- |
| Repository type       | Container registry → Amazon ECR          |
| Container image URI   | `<ECR_URI>:latest` (Browse finds it)     |
| Deployment trigger    | Manual                                   |
| ECR access role       | Create new service role                  |

**Configure service**

| Field                     | Value                                             |
| ------------------------- | ------------------------------------------------- |
| Service name              | `rbac-poc`                                         |
| Virtual CPU / memory      | 1 vCPU · 2 GB                                      |
| Port                      | **8080**                                           |
| Start command             | leave blank — the image has an `ENTRYPOINT`        |

Environment variables — add all three with **Source: Secrets Manager**, pasting each
secret's ARN as the value:

| Name                | Secret                    |
| ------------------- | ------------------------- |
| `DATABASE_URL`      | `rbac-poc/DATABASE_URL`   |
| `ANTHROPIC_API_KEY` | `rbac-poc/ANTHROPIC_API_KEY` |
| `JWT_SECRET`        | `rbac-poc/JWT_SECRET`     |

`aws secretsmanager list-secrets --region $AWS_REGION --query 'SecretList[].[Name,ARN]' --output table`
prints them.

Then, under **Additional configuration**:

- **Security → Instance role**: `RbacPocAppRunnerInstanceRole`
- **Auto scaling**: custom configuration, **minimum size 1**
- **Health check**: Protocol **HTTP**, Path **`/api/health`**, interval 10s, timeout 5s,
  unhealthy threshold 5
- **Networking**: Public endpoint, **Outgoing traffic: Public access** — the default, and
  what lets the Anthropic calls out

Create the service. The first deployment takes a few minutes.

> **Minimum size 1** is not just about cold starts. The entrypoint runs `alembic upgrade
> head` and `python seed.py --if-empty` on every instance that boots; holding at one
> instance keeps two containers from racing to migrate the same fresh database.

## 8. Verify

```bash
export APP_URL=$(aws apprunner list-services --region $AWS_REGION \
  --query "ServiceSummaryList[?ServiceName=='rbac-poc'].ServiceUrl" --output text)

curl -s https://$APP_URL/api/health
```

`status: ok` with `llm_configured: true` means the key arrived. Then open
`https://$APP_URL` and sign in as `superadmin@example.com` / `password123`, and check
`https://$APP_URL/demo` renders.

If the service never goes healthy, the application logs in the App Runner console show
the entrypoint's output — a database it cannot reach fails at `alembic upgrade head`
before uvicorn ever starts, which is the usual cause.

## 9. Redeploying

```bash
docker build --platform linux/amd64 --provenance=false -t rbac-poc:latest .
docker tag rbac-poc:latest $ECR_URI:latest
docker push $ECR_URI:latest

aws apprunner start-deployment --region $AWS_REGION --service-arn \
  $(aws apprunner list-services --region $AWS_REGION \
    --query "ServiceSummaryList[?ServiceName=='rbac-poc'].ServiceArn" --output text)
```

`--if-empty` means a redeploy leaves saved conversations and any runtime access grants
alone. To reset the demo data back to the seeded baseline, run `python seed.py` (no flag)
against `DATABASE_URL` from your own machine.

## Before you expose this

Two things are true of this deployment the moment it is live, and neither is a bug in the
POC — they are what the demo is built for:

1. **The demo credentials are published.** Anyone with the URL can sign in as
   `superadmin@example.com` with `password123`; the login page prefills it. Setting
   `SEED_PASSWORD` before the first boot changes what `seed.py` hashes, but the login
   page's demo buttons still send `password123` literally, so you would then have to type
   the real password by hand.
2. **The database accepts connections from anywhere**, guarded by the master password and
   TLS only.

Both are fine for a demo you tear down. Neither is fine for something left running with
real data. If this becomes more than a demo, switch to the VPC-connector path: RDS in
private subnets, an App Runner VPC connector, and a NAT Gateway carrying the LLM calls.

## Tearing it down

App Runner bills for provisioned memory even while idle, and RDS bills continuously.

```bash
aws apprunner delete-service --region $AWS_REGION --service-arn \
  $(aws apprunner list-services --region $AWS_REGION \
    --query "ServiceSummaryList[?ServiceName=='rbac-poc'].ServiceArn" --output text)

aws rds delete-db-instance --region $AWS_REGION \
  --db-instance-identifier rbac-poc-db --skip-final-snapshot --delete-automated-backups

aws ecr delete-repository --region $AWS_REGION --repository-name rbac-poc --force

for s in DATABASE_URL ANTHROPIC_API_KEY JWT_SECRET; do
  aws secretsmanager delete-secret --region $AWS_REGION \
    --secret-id rbac-poc/$s --force-delete-without-recovery
done

aws iam delete-role-policy --role-name RbacPocAppRunnerInstanceRole --policy-name RbacPocReadSecrets
aws iam delete-role --role-name RbacPocAppRunnerInstanceRole
```

Delete the `rbac-poc-db-sg` security group once RDS has finished releasing it.
