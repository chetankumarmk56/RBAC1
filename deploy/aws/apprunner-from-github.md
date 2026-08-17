# Deploying to App Runner from GitHub, without IAM permissions

[README.md](README.md) in this directory builds the [Dockerfile](../../Dockerfile), pushes it
to ECR and points App Runner at the image. That path needs three IAM roles: the ECR access
role, the instance role that reads Secrets Manager, and the service-linked role App Runner
creates for itself.

**This guide is the same service without the first two.** App Runner can build directly from
a GitHub repository using a managed Python runtime, and a source-based service needs no
access role — a GitHub *connection* replaces it — and no instance role, as long as you are
willing to hold the secrets as plain environment variables.

What that costs you is written down under [What this path gives up](#what-this-path-gives-up).
Read it before you start; one of the two items is a real secret-handling regression.

---

## 0. First: can this account still create App Runner services?

AWS **closed App Runner to new customers on April 30, 2026**. Accounts that were already
using it keep full access, including creating new services; accounts that were not cannot
create one at all. Nothing below works if this account falls on the wrong side of that line,
so check before doing any other work:

```bash
aws apprunner list-services --region ap-south-1
```

An empty `ServiceSummaryList` is *not* an answer — it only means no service exists yet. Open
the [App Runner console](https://console.aws.amazon.com/apprunner/home) and look at the
**Create service** button. If the console tells you the service is closed to new customers,
or creation fails with an authorization or opt-in error, stop here and read
[If the account cannot use App Runner](#if-the-account-cannot-use-app-runner).

AWS's own replacement, **ECS Express Mode**, does not help here: it takes a container image
and two IAM roles you would have to create.

## 1. Prerequisites

- A PostgreSQL database reachable from the internet, and its connection URL — see step 2.
- Node and Python locally, to build the frontend once.
- Push access to `origin` (`https://github.com/chetankumarmk56/RBAC1.git`) and a GitHub
  account that can install a GitHub App on it.
- Enough App Runner permission on your IAM user — `AWSAppRunnerFullAccess` covers it. The
  one part of it you cannot work around is `iam:CreateServiceLinkedRole`: App Runner creates
  `AWSServiceRoleForAppRunner` the first time an account creates a service, and service
  creation fails without it. If that is what your missing IAM permission turns out to be,
  ask whoever administers the account to create that one role — it is a service-linked role
  with a fixed AWS-managed policy, not a role that grants anybody new access.

The AWS CLI is optional here; every step can be done in the console.

## 2. Get a database

App Runner does not run databases, and the container's disk does not survive a deployment,
so PostgreSQL has to live somewhere else. Two options, neither of which needs an IAM role:

- **Amazon RDS**, if your user can create RDS instances — follow
  [README.md § 3](README.md#3-create-the-database) exactly as written, then come back. It
  produces a publicly reachable instance and an endpoint hostname.
- **A hosted Postgres outside AWS** (Neon, Supabase and friends all have a free tier), if
  your user cannot. You get a connection URL from their dashboard and AWS never enters into
  it.

Either way you end up with one value:

```
postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

Keep `sslmode=require` — the database is open to the internet either way, because App Runner
has no fixed egress IPs to allow-list. [config.py](../../backend/config.py) rewrites the
`postgresql://` scheme to `postgresql+psycopg://` on the way in and leaves the query string
alone, so paste the URL exactly as the provider gives it to you.

Nothing here creates the schema. The start command does that on first boot.

## 3. Build the frontend and commit it

This is the one real difference from the Docker path, and the reason it cannot be skipped:
App Runner's managed Python runtime image contains Python and nothing else. There is no Node
in it, so `vite build` cannot run during the App Runner build the way it does in
[stage 1 of the Dockerfile](../../Dockerfile). The built app is therefore committed to the
repository, and rebuilding it is a release step.

From the repository root:

```bash
cd frontend && npm ci && npm run build && cd ..
rm -rf backend/static && cp -r frontend/dist backend/static
git add -A && git commit -m "Build the frontend for the App Runner source deploy" && git push
```

[main.py](../../backend/main.py) mounts `backend/static` at `/` when the directory exists, so
FastAPI serves the React app and `/api` from one origin — same as the image does, and the
reason there is still no CORS configuration to keep in sync.

Re-run those three lines whenever anything under `frontend/` changes. Forgetting to is the
failure mode of this path: the deploy succeeds, and the site serves the previous frontend.

**`backend/static` must stay out of [.dockerignore](../../.dockerignore).** App Runner's
revised build is a Docker build over the cloned repository, so that file applies here too,
and it was written for the image — where excluding the bundle is free because the Vite stage
rebuilds it. Exclude it here and the deployment succeeds, the health check passes, and `/`
answers `{"detail":"Not Found"}` because `main.py` found no `static/` to mount.

## 4. Connect App Runner to GitHub

In the [App Runner console](https://console.aws.amazon.com/apprunner/home), **Create service**
→ **Source code repository** → **Add new** next to the connection field. That sends you to
GitHub to install the **AWS Connector for GitHub** app; grant it access to `RBAC1` only,
rather than every repository. Back in the console the connection appears as `Available`.

This connection is what stands in for the ECR access role. Creating it needs no IAM
permission of yours — the authorization happens on the GitHub side.

## 5. Create the service

**Source and deployment**

| Field              | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| Repository type    | Source code repository                                       |
| Connection         | the one from step 4                                          |
| Repository         | `chetankumarmk56/RBAC1`                                      |
| Branch             | `main`                                                       |
| Source directory   | `/`                                                          |
| Deployment trigger | Automatic — every push to `main` redeploys                   |

**Configure build**

| Field              | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| Configuration file | **Configure all settings here** — not `apprunner.yaml`       |
| Runtime            | **Python 3.11**                                              |
| Build command      | `pip3 install -r backend/requirements.txt --target deps`     |
| Start command      | `sh deploy/aws/apprunner-start.sh`                           |
| Port               | `8080`                                                       |

Three of those five deserve an explanation.

**Runtime must be Python 3.11, not "Python 3".** They are different runtimes in that
dropdown: `python3` is 3.7/3.8, both past end of support since December 2025, and the backend
uses `X | None` annotations that are a syntax error before 3.10.

**`--target deps` is not decoration.** App Runner's revised build for Python 3.11 keeps only
what the build wrote *inside* the source directory, and `pip3 install` writes to site-packages
outside it, where the result is discarded before the container runs.
[apprunner-start.sh](apprunner-start.sh) puts `deps/` on `PYTHONPATH` and `deps/bin` on `PATH`,
which is why `alembic` resolves at boot.

**"Configure all settings here", not a configuration file.** An `apprunner.yaml` would take
over the environment variables too, which would mean committing the Anthropic key to git.
Choosing the console keeps the secrets out of the repository — see step 6.

**Configure service**

| Field                | Value          |
| -------------------- | -------------- |
| Service name         | `rbac-poc`     |
| Virtual CPU / memory | 1 vCPU · 2 GB  |

Environment variables — **Plaintext** source for all of them, since Secrets Manager
references require the instance role this path does without:

| Name                | Value                                                   |
| ------------------- | ------------------------------------------------------- |
| `DATABASE_URL`      | the URL from step 2                                      |
| `ANTHROPIC_API_KEY` | `sk-ant-…`                                               |
| `JWT_SECRET`        | any long random string                                   |
| `GEMINI_API_KEY`    | optional — the fallback provider, blank disables it      |

Then, under **Additional configuration**:

- **Security → Instance role**: leave empty. The application calls no AWS APIs.
- **Auto scaling**: custom configuration, **minimum size 1**
- **Health check**: Protocol **HTTP**, Path **`/api/health`**, interval 10s, timeout 5s,
  unhealthy threshold 5
- **Networking**: Public endpoint, **Outgoing traffic: Public access** — the default, and what
  lets the Anthropic calls out without a VPC connector and its NAT Gateway

Create the service. The first deployment takes several minutes; the build log and the
application log are both in the console under **Logs**.

> **Minimum size 1** is not only about cold starts. The start command runs `alembic upgrade
> head` and `python seed.py --if-empty` on every instance that boots, and holding at one
> instance keeps two containers from racing to migrate the same fresh database.

## 6. Verify

```bash
curl -s https://<service-url>/api/health
```

`status: ok` with `llm_configured: true` means the key arrived. Then open the service URL and
sign in as `superadmin@example.com` / `password123`, and check `/demo` renders.

If the service never goes healthy, read the **application** log rather than the build log. A
database it cannot reach fails at `alembic upgrade head` before uvicorn ever starts, which is
the usual cause; `ModuleNotFoundError` instead means the build command lost its `--target
deps`.

## 7. Redeploying

With **Automatic** deployments, `git push` to `main` is the whole procedure — App Runner
rebuilds from source. Remember step 3 first if the frontend changed.

`--if-empty` means a redeploy leaves saved conversations and any runtime access grants alone.
To reset the demo data to the seeded baseline, run `python seed.py` (no flag) against
`DATABASE_URL` from your own machine.

## What this path gives up

1. **The secrets are plaintext service configuration.** Anyone with console read access to
   this account can read the Anthropic key and the database password off the service's
   configuration tab. The ECR path in [README.md](README.md) keeps them in Secrets Manager
   and hands App Runner an instance role that can read them; that requires creating an IAM
   role, which is exactly what this path assumes you cannot do. If you later get the
   permission, moving is one role and three console fields.
2. **The built frontend lives in git.** Every frontend change is now two commits' worth of
   diff — the source and the bundle — and a stale `backend/static` deploys silently.

Both are acceptable for a demo. Neither is something to carry into anything real.

Everything in [README.md § Before you expose this](README.md#before-you-expose-this) still
applies too: the demo credentials are published on the login page, and the database accepts
connections from anywhere.

## If the account cannot use App Runner

If this account was not an App Runner customer before April 30, 2026, the service cannot be
created and no configuration change works around it. The options that stay close to "one
managed service, no IAM roles to create":

- **Amazon Lightsail containers.** Deploys a container image with no IAM role of any kind —
  `aws lightsail push-container-image` uploads the image built from this repository's
  Dockerfile — and Lightsail has managed PostgreSQL alongside it. It is the closest
  equivalent still open to new accounts, and it needs only `lightsail:*` permission.
- **ECS Express Mode**, AWS's stated replacement, if someone can create the two IAM roles it
  requires (`ecsTaskExecutionRole` and `ecsInfrastructureRoleForExpressServices`).
- **Off AWS entirely.** The Dockerfile in this repository is a plain single-port container
  and runs unmodified anywhere that takes one.

## Tearing it down

App Runner bills for provisioned memory even while idle. Delete the service from the console,
or:

```bash
aws apprunner delete-service --region ap-south-1 --service-arn \
  $(aws apprunner list-services --region ap-south-1 \
    --query "ServiceSummaryList[?ServiceName=='rbac-poc'].ServiceArn" --output text)
```

Then delete the database wherever it lives, and the GitHub connection if you have no other
use for it. Uninstalling the AWS Connector for GitHub app from the repository is a separate
step, on GitHub.
