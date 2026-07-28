# Deploy to Azure Container Apps (GitHub Student $100 credit)

The face+palm service, at **full accuracy** (the `buffalo_l` face pack + full palm
pipeline + liveness that HF's 512 MB forced you to cut), on **2 vCPU / 4 GB** - no
OOM, no split accounts, no HF token/dataset pain. Verification is CPU-bound, so the
2 dedicated vCPUs fix the slow enroll/verify you had on HF's throttled shared cores.

**Cost:** scale-to-zero during the pilot keeps it near-$0 (a logo loading screen
covers the ~15 s wake). Your $100 / 365-day student credit lasts the whole year;
when you go live and it's used constantly it simply never sleeps.

---

## What you run once (interactive - the `!` steps go in the Claude prompt)

### 0. Install the Azure CLI (once)
Windows: `winget install -e --id Microsoft.AzureCLI` (then reopen the shell).

### 1. Log in
```
! az login
```
A browser opens; pick the **student** account (`kyereboatengcaleb@…`). If you have
more than one subscription: `az account set --subscription "Azure for Students"`.

### 2. Create a GHCR read token
The container image lives in **GitHub Container Registry** (free, your pack). Make a
token with **`read:packages`** at <https://github.com/settings/tokens> - Azure uses
it to pull the image. Keep it handy for the next step.

### 3. Provision everything (idempotent)
```
pip install pyyaml            # the script does one small YAML patch
./deploy-azure.sh <your-github-username-lowercase> <the-read:packages-token>
```
This creates the resource group, an Azure Files share for durable `/data`, the
Container Apps environment, and the app itself - then prints the live URL. Safe to
re-run; every step is create-if-missing.

> First run needs the image to exist in GHCR. If you haven't pushed it yet, run the
> GitHub Action first (step 5) - or build & push locally:
> ```
> docker build -t ghcr.io/<user>/verification-system:latest .
> echo <read:packages-token> | docker login ghcr.io -u <user> --password-stdin
> docker push ghcr.io/<user>/verification-system:latest
> ```

### 4. Wire up automatic deploys (CI) - optional but recommended
So every `git push` rebuilds and rolls out:

```
# a service principal scoped to the resource group, for GitHub to log in with
az ad sp create-for-rbac --name verify-ci \
  --role contributor \
  --scopes /subscriptions/$(az account show --query id -o tsv)/resourceGroups/verify-rg \
  --sdk-auth
```
Copy the JSON it prints into a repo secret named **`AZURE_CREDENTIALS`**
(GitHub → repo → Settings → Secrets and variables → Actions → New secret).
The built-in `GITHUB_TOKEN` already pushes to GHCR - no other secret needed.

### 5. Ship
`git push` to `main` (or run the **deploy-azure** workflow from the Actions tab).
It builds → pushes to GHCR → updates the app.

---

## Custom domain (`verify.kyere.me`)
The `*.azurecontainerapps.io` URL is unreadable on a poster and unmemorable on a
phone. Bind a subdomain of a domain you own - Azure issues a free managed TLS
certificate for it, so there is nothing to renew.

1. At your DNS host (Namecheap → Domain List → **Advanced DNS** → Add New Record),
   add both records. `./bind-domain.sh` prints them with your real values:

   | Type | Host | Value |
   |---|---|---|
   | `CNAME` | `verify` | `<app>.<region>.azurecontainerapps.io` |
   | `TXT` | `asuid.verify` | the app's `customDomainVerificationId` |

2. Bind it (waits for DNS, then issues the cert):
```
./bind-domain.sh                    # verify.kyere.me
./bind-domain.sh admin.kyere.me     # any further subdomain
```

The old `*.azurecontainerapps.io` URL keeps working - a bound hostname is added
alongside it, not swapped in - so QR codes and APKs already pointing at it don't
break the moment you cut over.

## Going live (never sleep)
When real traffic is constant and you want zero cold starts:
```
az containerapp update -n verify -g verify-rg --min-replicas 1
```

## Watching it
```
az containerapp logs show -n verify -g verify-rg --follow
az containerapp show  -n verify -g verify-rg \
  --query "properties.configuration.ingress.fqdn" -o tsv
```

## Notes / knobs
| Env var | Set to | Why |
|---|---|---|
| `BIO_SQLITE_JOURNAL` | `DELETE` | WAL can't run on the Azure Files (SMB) mount; `DELETE` + single replica is safe. Set by the script. |
| `FACE_OPEN_ENROLL` | `1` | Pilot walk-up enrol (no operator password). Set `0` to lock down. |
| `FACE_FIELD_DATA` | `1` | Record every capture under `/data/fielddata` for accuracy tuning. |
| *(unset)* `FACE_MODEL_NAME` | - | Leave unset → full `buffalo_l`. (HF set `buffalo_s` to fit 512 MB.) |
| *(unset)* `FACE_ACTIVE_LIVENESS` | - | Leave unset → liveness ON (default). |
| **`AZ_MAX_REPLICAS`** | **1** | Keep at 1: a single SQLite writer over SMB. Do not raise without moving off SQLite. |

## Credit safety
The student subscription **never auto-converts to pay-as-you-go** - when the $100 or
365 days runs out the app just disables; it cannot bill you. Optionally set a $0
budget alert in **Cost Management** for peace of mind.
