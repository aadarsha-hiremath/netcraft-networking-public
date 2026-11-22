# Clean Windows 11 x64 acceptance

Use a fresh, fully patched Windows 11 x64 VM and a standard local user.
Record edition, build, architecture, Defender and SmartScreen state, and
whether the Microsoft VC++ x64 runtime is present in Installed Apps. Take a
checkpoint before testing. Leave Python, Qt, development tools and any added
VC++ runtime uninstalled for the initial launch. Test with the regular
Windows security settings and keep the original checkpoint intact.

1. Transfer the candidate ZIP and the approved SHA-256 through a trusted
   channel. Record whether the transfer preserves Windows download marking
   (`Zone.Identifier`). Compare the checksum before extraction. Inspect
   Authenticode on the extracted launcher. Capture the exact result and any
   SmartScreen or policy prompt, including whether launch was blocked. Keep
   the blocking policy in place if a launch is blocked.

   ```powershell
   $zip = 'C:\QA\NetCraft-0.5.0-win-x64.zip'
   Get-FileHash -Algorithm SHA256 -LiteralPath $zip
   Get-Item -LiteralPath $zip -Stream Zone.Identifier -ErrorAction SilentlyContinue
   Expand-Archive -LiteralPath $zip -DestinationPath 'C:\QA\Relocated Candidate'
   $bundle = 'C:\QA\Relocated Candidate\NetCraft-0.5.0-win-x64'
   Get-AuthenticodeSignature -LiteralPath "$bundle\NetCraft.exe"
   ```

2. Launch `NetCraft.exe` from Explorer before introducing any QA tools.
   Verify that first launch opens an idle Overview with empty History and
   no monitoring targets. Check all seven pages and record the Settings
   workspace location. Record whether the application creates files outside
   the extracted folder. Close using the title-bar button and verify the
   launcher and its bundled `pythonw.exe` child both exit. Repeat launch.
3. Using PowerShell and the extracted Python, run the packaged TCP and UDP
   echo commands. Check JSON for `success` and the expected echo bytes.

   ```powershell
   Set-Location -LiteralPath $bundle
   & .\runtime\python.exe -I -B -m netcraft tcp-echo 'clean vm tcp' --json
   & .\runtime\python.exe -I -B -m netcraft udp-echo 'clean vm udp' --json
   ```

4. After the fresh launch checks, introduce an **isolated QA fixture kit**
   in `C:\QA\fixture-kit`, outside the bundle and with no PATH changes. Use
   a separate embeddable Python or test environment containing the Phase 5.1
   source checkout, its fixture dependencies from the `dev` extra, and
   dnspython. From that checkout set `PYTHONPATH` to its absolute root and
   use the kit's Python to run `packaging/windows/verify_fixtures.py` with
   `C:\QA\fixture-state`. Keep the fixture running and record the dynamically
   assigned ports from `endpoints.json`. The peer services and disposable
   CA stay inside the guest. Use the candidate's Python for the tests in a
   separate terminal. Use Windows PowerShell or PowerShell 7 locally.

   ```powershell
   $python = Join-Path $bundle 'runtime\python.exe'
   $qa = 'C:\QA\fixture-kit\source\packaging\windows'
   $env:QT_QPA_PLATFORM = 'windows'
   & $python -I -B "$qa\verify_portable.py" exercise 'C:\QA\widget-state' 'C:\QA\fixture-state'
   & $python -I -B "$qa\verify_portable.py" reopen 'C:\QA\widget-state' 'C:\QA\fixture-state'
   & $python -I -B "$qa\verify_portable.py" stress 'C:\QA\stress-state' 'C:\QA\fixture-state'
   New-Item -ItemType File -Path 'C:\QA\fixture-state\finished'
   ```

5. Independently exercise the native Save As dialog on throwaway data.
   Export a new CSV and JSON. Decline replacement and compare the original
   SHA-256, then accept replacement and open the new file. Cancel a live
   monitor probe, reopen the app, and confirm the expected rows survive.
   Save the fixture logs and `PRAGMA integrity_check` and
   `PRAGMA foreign_key_check` results from the harness.
6. Record exact ZIP hash, exit codes, startup or loading failures, and
   screenshots of the clean launch, monitors, and Save As result. If a
   DLL fails to load, record the missing DLL and VM state. Any retry after
   installing a Microsoft prerequisite gets a separate result. A relocated
   test on a development machine receives its own result.
