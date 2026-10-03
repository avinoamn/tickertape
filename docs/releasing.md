# Releasing

How a version of tickertape is cut, what the release workflow does, and how to recover when it fails. For deploying a release to a cluster see [operations.md](operations.md).

## Versioning

- **One SemVer for the whole repository.** `MAJOR.MINOR.PATCH`, tagged `vX.Y.Z` on `main`. The three services, the chart and the docs are released together: they are tested together, and one number is easier to reason about than three.
- **Images** are published as `ghcr.io/<owner>/tickertape-poller`, `-ner` and `-laya`, tagged with the version only (`0.2.0`). There is no `latest`, and a tag is never moved or re-pushed.
- **The chart** `charts/tickertape` has `version` and `appVersion` equal to the release. The image tags in its `values.yaml` are empty, which means "use `appVersion`", so deploying a chart at a tag deploys the images of that tag.
- **The Laya model has its own version**: the Hugging Face commit pinned in `values.yaml` (`laya.revision`). Retraining and rolling out a new model is a values change, not necessarily a code release, but cutting a release is the clean way to record it.
- **Pre-releases** such as `v0.3.0-rc.1` follow the same process and are marked as pre-releases on GitHub.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/), and `CHANGELOG.md` records what changed per release ([Keep a Changelog](https://keepachangelog.com/) format).

What counts as a bump: a breaking change to the schema, the configuration or the deployment procedure is a MAJOR (MINOR while the version is 0.x); new behaviour is a MINOR; fixes are a PATCH.

## Cutting a release

1. **Prepare in a pull request** titled `chore(release): X.Y.Z`:
   - set `version` and `appVersion` in `charts/tickertape/Chart.yaml` to `X.Y.Z`;
   - in `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a fresh empty `## [Unreleased]` above it;
   - make sure the docs match what ships.
2. **Merge it** (CI must pass). The release is the merged commit.
3. **Tag that commit on `main`** and push the tag:
   ```sh
   git switch main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z
   ```
4. **Watch the workflow** (Actions, "Release"). When it is green there is a GitHub Release with the changelog entry and the packaged chart, and three public images.
5. **Deploy it** ([operations.md](operations.md)): `make deploy` from the tag's checkout upgrades the cluster to the images of that tag.

## What the release workflow does

`.github/workflows/release.yml`, triggered by a `v*.*.*` tag:

1. **Verify**, before anything is pushed: the tag is valid SemVer; `Chart.yaml`'s `version` and `appVersion` equal it; `CHANGELOG.md` has a section for it; the tagged commit is on `main`; and the CI workflow succeeded for that exact commit.
2. **Images**: builds `poller`, `ner` and `laya` for `linux/amd64` and pushes them with the version tag and OCI labels (source repository, version, revision, creation time, license). Layer caching is shared with CI, so a release of unchanged dependencies takes a couple of minutes.
3. **Chart**: `helm lint` and `helm package`.
4. **Public pull check**: looks each image up without credentials, because a cluster pulls anonymously.
5. **GitHub Release**: notes taken from the changelog section, the image names, and the chart archive attached.

Permissions are per job and minimal: only the image job can write packages, only the release job can write contents.

### Rehearsal

Running the workflow manually (Actions, "Release", Run workflow, or `gh workflow run release.yml`) does steps 1 to 3 against whatever `Chart.yaml` currently says, without pushing images and without creating a release. Use it to check a change to the workflow itself.

## One-time setup

- **Make the packages public.** GHCR creates a package as private the first time it is pushed. After the first release, for each of `tickertape-poller`, `tickertape-ner` and `tickertape-laya`: GitHub profile, Packages, the package, Package settings, Change visibility, Public. The public pull check fails with exactly this instruction until it is done; after changing the setting, re-run that job and the release continues. Once public, a cluster pulls the images without any Secret.

## When something fails

| Where it failed | What to do |
|---|---|
| **Verify** (tag format, `Chart.yaml`, changelog, not on main, CI not green) | Nothing was published. Delete the tag (`git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`), fix it in a PR, and tag again. |
| **Image build** | Nothing was published for that image. Fix and re-run the failed job, or delete the tag and start over if the fix needs a new commit. |
| **Public pull check** | Make the packages public (above) and re-run the job. |
| **After the images were pushed** (release creation failed, or you found a bug) | Do not move or reuse the tag: its images exist and someone may have pulled them. Re-run the failed job if it is only the release step; otherwise fix forward with the next patch version. |

## Rolling back a deployment

A release is immutable, so going back means deploying an older one: `scripts/helm.sh history tickertape` and `scripts/helm.sh rollback tickertape <revision>`, or deploy the previous tag.
