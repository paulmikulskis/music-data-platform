#!/usr/bin/env bash
# Push one branch and prove its PR checks before review. Never merge.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
repo="${GITHUB_REPO:?Set GITHUB_REPO}"
pr_url=
body_file=
finish() {
  code=$?
  if [[ -n "$body_file" ]]; then rm -f "$body_file"; fi
  if [[ -n "$pr_url" ]]; then printf 'PR %s\n' "$pr_url"; fi
  if (( code != 0 )); then
    printf 'Gate did not pass. Fix the reported check, then run ops/merge-gate.sh <branch>.\n' >&2
  fi
}
trap finish EXIT
if [[ $# != 1 || "$1" == main ]]; then
  echo 'Choose a feature branch: ops/merge-gate.sh <branch>.' >&2
  exit 2
fi
branch=$1
git check-ref-format "refs/heads/$branch"
if [[ "$(git branch --show-current)" != "$branch" ]]; then
  echo 'The branch must be checked out. Run git switch <branch>, then run ops/merge-gate.sh <branch>.' >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'The checkout has uncommitted files. Run git status and commit them, then run ops/merge-gate.sh <branch>.' >&2
  exit 2
fi
sha=$(git rev-parse "refs/heads/$branch")
git fetch origin main
# Push the pinned commit, not a branch ref another process could move.
git push origin "$sha:refs/heads/$branch"
pr_url=$(gh pr list --repo "$repo" --base main --head "$branch" --state open --json url --jq '.[0].url // empty')
if [[ -z "$pr_url" ]]; then
  body_file=$(mktemp /tmp/mdp-merge-gate-XXXXXX)
  cat > "$body_file" <<'BODY'
This branch waits for all required CI checks before review.
Open the Checks tab, then review the diff before merging into main.
BODY
  pr_url=$(gh pr create --repo "$repo" --base main --head "$branch" --draft \
    --title "Check $branch before main" --body-file "$body_file")
fi
printf 'PR %s; open the Checks tab.\n' "$pr_url"
# GitHub can briefly report the head from before the push. Allow 60 seconds to catch up.
for attempt in {0..20}; do
  read -r pr_head pr_base < <(gh pr view "$pr_url" --repo "$repo" \
    --json headRefOid,baseRefOid --jq '[.headRefOid, .baseRefOid] | @tsv')
  if [[ "$pr_head" == "$sha" || ! "$pr_base" =~ ^[0-9a-f]{40}$ ]]; then break; fi
  if (( attempt < 20 )); then sleep 3; fi
done
if [[ "$pr_head" != "$sha" || ! "$pr_base" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'The PR does not point to the pinned commit. Run ops/merge-gate.sh <branch> again.' >&2
  exit 1
fi
bash ops/ci-wait.sh "$sha" --pr-base "$pr_base"
pr_head=$(gh pr view "$pr_url" --repo "$repo" --json headRefOid --jq '.headRefOid')
if [[ "$pr_head" != "$sha" || "$(git rev-parse "refs/heads/$branch")" != "$sha" ]]; then
  echo 'The branch moved while checks ran. Run ops/merge-gate.sh <branch> again.' >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'The checkout changed while checks ran. Run git status, commit the changes, then run ops/merge-gate.sh <branch>.' >&2
  exit 1
fi
printf 'Checks pass for %s. Next: review %s before merging.\n' "$sha" "$pr_url"
