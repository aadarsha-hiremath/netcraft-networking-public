# NetCraft v0.5.0 source publication checklist

The release consists of the GitHub-generated source ZIP and tarball. The
Windows portable ZIP and installer are unavailable for this release.
[Source-only release notes](release-notes-0.5.0.md)

## Verify the public repository

- Use the prepared repository with one root commit on main. Keep the private
  development repository and its full history separate.
- Confirm the README, MIT license and Phase 4.5 owner acceptance are present.
  The two Phase 4.4 Settings captures that exposed a local account path are
  excluded. The public documentation retains the remaining evidence.
- Compare the exact source commit and archive SHA-256 values with the local
  verification report. Review the regression, static, package and privacy
  checks and the independent fresh clone results.
- Obtain approval for a specific empty GitHub repository. Verify ownership,
  visibility, and the absence of branches and tags with an authenticated
  read-only check. The private development remote is outside this scope.

[Phase 4.5 owner acceptance](phase-4-5-final-desktop-acceptance.md) ·
[GitHub source archives](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives)

## Initial upload after approval

Run the following in PowerShell 7 from the prepared public repository.
Set the URL and commit from the owner-approved verification report.

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$destination = 'https://github.com/<owner>/<approved-empty-repository>.git'
$expectedCommit = '<verified-root-commit>'
if ((git rev-parse HEAD).Trim() -ne $expectedCommit) { throw 'Unexpected commit' }
if ((git branch --show-current).Trim() -ne 'main') { throw 'Unexpected branch' }
if ((git rev-list --count --all).Trim() -ne '1') { throw 'Unexpected history' }
if (git status --porcelain) { throw 'Working tree has changes' }
if (git remote) { throw 'Verify the configured remotes before proceeding' }
$remoteRefs = @(git ls-remote --symref $destination)
if ($remoteRefs.Count -ne 0) { throw 'The approved destination has refs' }
git remote add origin $destination
git push --dry-run origin refs/heads/main:refs/heads/main
if (@(git ls-remote --symref origin).Count -ne 0) { throw 'The destination changed' }
git push --set-upstream origin refs/heads/main:refs/heads/main
git ls-remote origin refs/heads/main
```

The read-only check must exit successfully. A declined push or a changed
destination requires a fresh review. [GitHub import instructions](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)

## Tag and release after separate approval

Verify that remote main points to the approved root commit and that the
tag is absent locally and remotely.

```powershell
$remoteMain = @(git ls-remote origin refs/heads/main)
if ($remoteMain -notcontains "$expectedCommit`trefs/heads/main") { throw 'Remote main differs' }
if (git tag --list v0.5.0) { throw 'Local release tag exists' }
if (@(git ls-remote origin refs/tags/v0.5.0).Count -ne 0) { throw 'Remote release tag exists' }
git tag -a v0.5.0 $expectedCommit -m 'NetCraft v0.5.0 source release'
if ((git rev-list -n 1 v0.5.0).Trim() -ne $expectedCommit) { throw 'Unexpected tag target' }
git push origin refs/tags/v0.5.0:refs/tags/v0.5.0
```

Use the source-only release notes for the approved tag and leave the release
asset upload list empty. Verify the GitHub-generated downloads against the
reviewed tree. The portable ZIP has a separate distribution gate.
[Runtime and licensing audit](phase-5-1-runtime-audit.md)
