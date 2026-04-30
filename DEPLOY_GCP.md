# Deploy Meridian to Google Cloud Run

This repository ships:

- **`Dockerfile`** — Streamlit on port **`PORT`** (Cloud Run sets `PORT`, default `8080`).
- **`requirements-prod.txt`** — production dependencies (no `pytest`).
- **`.github/workflows/deploy-gcp.yml`** — on every push to **`main`** (and manual **`workflow_dispatch`**): run tests, build the image, push to **Artifact Registry**, deploy to **Cloud Run** using **Workload Identity Federation** (no long-lived JSON keys in GitHub).

---

## 1. Google Cloud prerequisites

1. **Create or pick a GCP project** and enable billing.
2. **Enable APIs** (Console → APIs & Services, or `gcloud`):

   ```bash
   gcloud services enable run.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com cloudresourcemanager.googleapis.com secretmanager.googleapis.com --project=YOUR_PROJECT_ID
   ```

3. **Choose a region** (e.g. `us-central1`) and use it consistently for Artifact Registry and Cloud Run.

---

## 2. Artifact Registry (Docker)

Create a Docker repository (one-time):

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
export REPO=meridian

gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --description="Meridian container images"
```

Image path shape:

`REGION-docker.pkg.dev/PROJECT_ID/REPO/IMAGE_NAME:TAG`

The workflow uses `IMAGE_NAME=meridian` (see workflow `env.IMAGE_NAME`).

---

## 3. Deployer service account (for GitHub Actions)

Create a dedicated deployer account (not your user account):

```bash
export DEPLOY_SA="github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create github-deploy \
  --project="${PROJECT_ID}" \
  --display-name="GitHub Actions deploy Meridian"
```

If you see **`Service account github-deploy already exists`**, skip creation—the account is already there. Confirm and continue with IAM grants below:

```bash
gcloud iam service-accounts describe "${DEPLOY_SA}" --project="${PROJECT_ID}"
```

Grant it:

- **Artifact Registry** — push images to your repo (e.g. `roles/artifactregistry.writer` scoped to the repository, or writer on the repo resource).
- **Cloud Run** — create/update services (`roles/run.admin` is common for small teams; tighter custom roles are possible).
- **Service Account User** — on the **Cloud Run runtime** service account so the deployer may create revisions that run as that identity. Default runtime SA is the Compute Engine default:

  ```bash
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

  gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="roles/iam.serviceAccountUser"
  ```

Adjust if you use a **custom runtime** service account for Cloud Run (`--service-account` on deploy); the deployer must have `roles/iam.serviceAccountUser` on **that** service account.

---

## 4. Workload Identity Federation (OIDC → GCP)

Full guide: [google-github-actions/auth — Workload Identity Federation](https://github.com/google-github-actions/auth#setting-up-workload-identity-federation).

### What `GCP_WORKLOAD_IDENTITY_PROVIDER` is

It is the **full resource name** of the **OIDC provider** inside a **Workload Identity Pool** (not the pool id alone). Shape:

```text
projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL_ID/providers/PROVIDER_ID
```

Use **`PROJECT_NUMBER`** (digits), not the project id string. Example:

```text
projects/123456789012/locations/global/workloadIdentityPools/github-pool/providers/github-provider
```

### Create pool + provider (`gcloud`)

Replace `PROJECT_ID`, `POOL_ID`, `PROVIDER_ID`, and GitHub `REPO` (`ORG_OR_USER/REPO_NAME`) as needed.

```bash
export PROJECT_ID="your-project-id"
export PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
export POOL_ID="github-pool"
export PROVIDER_ID="github-provider"
export GITHUB_REPO="YOUR_GITHUB_ORG/meridian"   # e.g. acme-corp/meridian

gcloud iam workload-identity-pools create "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"

# Let GitHub Actions from this repo impersonate the deployer service account
gcloud iam service-accounts add-iam-policy-binding "github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"
```

### Get the value for GitHub variable `GCP_WORKLOAD_IDENTITY_PROVIDER`

After the provider exists, print the string (same pattern Google shows in the console):

```bash
echo "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
```

Copy that **one line** into **GitHub → Settings → Secrets and variables → Actions → Variables → `GCP_WORKLOAD_IDENTITY_PROVIDER`**.

### Console alternative

1. **IAM & Admin** → **Workload Identity Federation** → open your **pool** → **Providers** → click the GitHub OIDC **provider**.  
2. Copy **Provider name** (full `projects/…/providers/…` path).

### Optional: restrict to branch `main` only

Tighten the `principalSet` / use a **condition** on the `workloadIdentityUser` binding so only `ref:refs/heads/main` can authenticate (see Google’s “attribute condition” examples in the link above).

### Also set

**`GCP_SERVICE_ACCOUNT_EMAIL`** = `github-deploy@PROJECT_ID.iam.gserviceaccount.com` (the SA that receives `workloadIdentityUser`).

No JSON key is stored in GitHub.

---

## 5. GitHub repository variables

In **Settings → Secrets and variables → Actions → Variables**, create:

| Variable | Example | Purpose |
|----------|---------|---------|
| `GCP_PROJECT_ID` | `my-proj` | GCP project id |
| `GCP_REGION` | `us-central1` | Region for Artifact Registry + Cloud Run |
| `GCP_ARTIFACT_REPOSITORY` | `meridian` | Artifact Registry **repository id** |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/…/providers/…` | WIF provider resource name |
| `GCP_SERVICE_ACCOUNT_EMAIL` | `github-deploy@….iam.gserviceaccount.com` | Deployer SA |
| `CLOUD_RUN_SERVICE` | `meridian-support` | Cloud Run service name |

Optional:

| Variable | Example | Purpose |
|----------|---------|---------|
| `GCP_OPENAI_SECRET_NAME` | `openai-api-key` | Secret Manager **secret id** (same project). If set, deploy adds `--set-secrets=OPENAI_API_KEY=SECRET_NAME:latest`. The **Cloud Run runtime** SA must have `secretAccessor` on that secret. |

---

## 6. Secrets and environment (runtime)

**Never commit** `OPENAI_API_KEY` or other secrets to the repo.

Recommended:

1. Create a Secret Manager secret (e.g. id `openai-api-key`) with the API key payload.
2. Grant **`roles/secretmanager.secretAccessor`** to the **Cloud Run service account** used by the revision (default compute SA unless you override).
3. Set GitHub variable **`GCP_OPENAI_SECRET_NAME`** so CI attaches it at deploy.

Other app settings (`MCP_SERVER_URL`, `MERIDIAN_*`, model overrides) can be added with:

```bash
gcloud run services update CLOUD_RUN_SERVICE \
  --region=REGION \
  --project=PROJECT_ID \
  --set-env-vars="MCP_SERVER_URL=https://…/mcp,MERIDIAN_LOG_FORMAT=json"
```

Or use **Secret Manager** / **YAML** for larger configs.

---

## 7. Manual first-time deploy (optional smoke test)

From the repo root, with Docker and `gcloud` authenticated as a user who can push to Artifact Registry and deploy Cloud Run:

```bash
export PROJECT_ID=…
export REGION=us-central1
export REPO=meridian
export SERVICE=meridian-support
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/meridian:test"

docker build -t "${IMAGE}" .
docker push "${IMAGE}"

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 2Gi \
  --concurrency 1 \
  --timeout 900 \
  --session-affinity
```

Open the printed **Service URL** and verify sign-in and support chat.

---

## 8. CI behavior

- **Trigger:** On every **push to `main`**, GitHub Actions runs this workflow. The **test** job always runs on that push; the **deploy** job runs only when `github.ref` is **`refs/heads/main`** (so pushes to other branches do not deploy, unless you add them under `on.push.branches` and extend the deploy `if`).
- **Test** job: Python 3.12, `pip install -r requirements.txt`, `pytest tests/ -q`.
- **Deploy** job: fails fast if required variables are missing; authenticates via WIF; builds/pushes `:GITHUB_SHA` and `:latest`; deploys Cloud Run with **concurrency 1** (required for Streamlit per instance), **session affinity**, **2Gi** memory, **900s** request timeout (agent + MCP calls).

**Manual deploy:** In GitHub → **Actions** → **Deploy to GCP (Cloud Run)** → **Run workflow** (`workflow_dispatch`). That can deploy from the branch you select (useful for hotfix branches); ensure that branch’s image is what you intend to ship.

If your default branch is not `main`, either rename it to `main` in the repository settings or edit `.github/workflows/deploy-gcp.yml` (`on.push.branches` and the deploy job `if` condition) to match your branch name.

---

## 9. Troubleshooting

| Symptom | Check |
|---------|--------|
| `Service account github-deploy already exists` | Normal if you ran create before—use `gcloud iam service-accounts describe github-deploy@PROJECT_ID.iam.gserviceaccount.com` and continue with IAM + WIF (§3–§5) |
| `Permission denied` on push | Deploy SA has Artifact Registry write on the repo |
| `Permission denied` on deploy | Deploy SA has `run.admin` (or equivalent) and `iam.serviceAccountUser` on runtime SA |
| `Could not resolve secrets` | Secret exists; runtime SA has `secretAccessor`; `GCP_OPENAI_SECRET_NAME` matches secret **id** |
| Blank / broken Streamlit | Service listens on `$PORT` (Dockerfile does); Cloud Run **ingress** allows traffic; try **session affinity** enabled (workflow enables it) |
| 403 from GitHub | WIF attribute condition matches your org/repo and branch |

For production hardening, consider **IAM-only** access (remove `--allow-unauthenticated`) and front with Identity-Aware Proxy or your IdP.
