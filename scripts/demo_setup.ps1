<#
.SYNOPSIS
    Gets the San Pascual HR prototype ready for a dry run or presentation. Fake data only.

.DESCRIPTION
    1. Checks Docker and .env.
    2. With -Reset: deletes the database and uploaded files, then starts clean (asks first).
    3. Starts the stack (migrations run automatically) and loads offices, roles and ~420 fake people.
    4. Creates the demo accounts once and prints their one-time passwords (shown once),
       and fills the calendar with sample events, holidays and leave around today.
       Later runs keep the passwords you set while rehearsing, unless -NewDemoPasswords.
    5. Writes sample upload files to demo_files\ for the document upload demo.
    6. Checks the site, the stylesheet, Django's system check and the audit chain.

    Then follow docs\DEMO.md.

.EXAMPLE
    pwsh -File scripts\demo_setup.ps1 -Reset
    Clean demo database (recommended before a presentation).

.EXAMPLE
    pwsh -File scripts\demo_setup.ps1 -RunTests -Lan
    Keep the current data, run the test suite first, and show how to open the demo from a phone.
#>
[CmdletBinding()]
param(
    [switch]$Reset,     # delete the database and uploads first (fake data only)
    [switch]$Force,     # with -Reset: skip the "type RESET" question (for unattended runs)
    [switch]$RunTests,  # run the full test suite first (about 3 minutes)
    [switch]$NewDemoPasswords,  # reissue one-time passwords for every demo account
    [switch]$Lan        # show how to open the demo from a phone on the same Wi-Fi
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$port = if ($env:WEB_PORT) { $env:WEB_PORT } else { "8000" }
$baseUrl = "http://localhost:$port"

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }

function Invoke-Manage {
    # manage.py in the one-shot "manage" container, which connects as the database owner role.
    docker compose run --rm manage python manage.py @args
    if ($LASTEXITCODE -ne 0) { throw "manage.py $($args -join ' ') failed (exit code $LASTEXITCODE)." }
}

function Get-ManageNumber($code) {
    $out = Invoke-Manage shell -v 0 -c $code
    return [int](($out | Select-Object -Last 1).Trim())
}

function Test-Url($url) {
    try { return (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 10).StatusCode } catch { return 0 }
}

# --- 1. Preflight -----------------------------------------------------------------
Write-Step "Checking Docker and .env"
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker is not running. Start Docker Desktop and run this script again." }
if (-not (Test-Path .env)) { throw ".env is missing. Copy .env.example to .env and replace every CHANGE-ME (see README)." }
$envText = Get-Content .env -Raw
if ($envText -match '(?m)^\s*\w+\s*=\s*CHANGE-ME\s*$') { throw ".env still contains CHANGE-ME values. Replace them first (see README)." }
if ($envText -notmatch '(?m)^\s*DJANGO_DEBUG\s*=\s*1\s*$') {
    throw "Demo accounts need DJANGO_DEBUG=1 in .env. Use that only for a local demo, never on a shared server."
}
Write-Ok "Docker is running and .env is filled in"

# --- 2. Optional reset --------------------------------------------------------------
if ($Reset) {
    Write-Step "Reset: delete the database and uploaded files"
    if (-not $Force) {
        Write-Warn "This permanently deletes this prototype's database, audit log and uploaded scans."
        $answer = Read-Host "    Type RESET to continue"
        if ($answer -cne "RESET") { throw "Reset cancelled. Nothing was deleted." }
    }
    docker compose down -v
    if ($LASTEXITCODE -ne 0) { throw "docker compose down -v failed." }
    Write-Ok "Database and uploads deleted"
}

# --- 3. Start the stack -----------------------------------------------------------
Write-Step "Starting the stack (builds the image and applies migrations)"
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed. Run 'docker compose logs manage' to see why." }
$deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Seconds 2
    $code = Test-Url "$baseUrl/accounts/login/"
} until ($code -eq 200 -or (Get-Date) -gt $deadline)
if ($code -ne 200) { throw "The site did not come up at $baseUrl. Run 'docker compose logs web' to see why." }
Write-Ok "Site is up at $baseUrl"

if ($RunTests) {
    Write-Step "Running the test suite (about 3 minutes)"
    Invoke-Manage test --noinput
    Write-Ok "All tests passed"
}

# --- 4. Data and accounts -----------------------------------------------------------
Write-Step "Loading offices, roles and fake personnel"
Invoke-Manage seed_offices | Out-Null
Invoke-Manage setup_roles | Out-Null
$people = Get-ManageNumber "from hr.models import Person; print(Person.objects.count())"
if ($people -eq 0) {
    Invoke-Manage seed_fake | Out-Null
    $people = Get-ManageNumber "from hr.models import Person; print(Person.objects.count())"
    Write-Ok "Loaded $people fake people"
} else {
    Write-Ok "$people people already loaded (kept as they are)"
    $unfunded = Get-ManageNumber "from hr.models import Project; print(Project.objects.filter(funding_reference='').count())"
    if ($unfunded -gt 0) {
        Write-Warn "$unfunded project(s) have no funding reference, so HRMO cannot hire onto them."
        Write-Warn "Add references as demo_projects, or rerun with -Reset for a clean dataset."
    }
}

$demoAccounts = Get-ManageNumber "from django.contrib.auth.models import User; print(User.objects.filter(username__startswith='demo_').count())"
if ($demoAccounts -eq 0 -or $NewDemoPasswords) {
    Write-Step "Creating demo accounts with one-time passwords"
    Invoke-Manage create_demo_users
    Write-Warn "Write these passwords down now; they are not shown again. Each account sets its own password at first login."
} else {
    Write-Step "Demo accounts"
    Write-Ok "$demoAccounts demo accounts already exist; their passwords are unchanged (use -NewDemoPasswords to reissue)"
}

Write-Step "Filling the calendar around today"
$calendar = Invoke-Manage seed_demo_calendar
Write-Ok (($calendar | Select-Object -Last 1) -replace '^Calendar demo data: ', '')

Write-Step "Filling empty org chart reporting lines"
$lines = Invoke-Manage seed_reporting_lines
Write-Ok ($lines | Select-Object -Last 1)

Write-Step "Salary actions: sample long careers, then the daily check"
$salary = Invoke-Manage seed_demo_salary_history
Write-Ok ($salary | Select-Object -Last 1)

# --- 5. Sample upload files -----------------------------------------------------------
Write-Step "Writing sample upload files to demo_files\"
New-Item -ItemType Directory -Force -Path demo_files | Out-Null
$stream = "BT /F1 20 Tf 72 760 Td (SAMPLE SCAN - FAKE DATA ONLY) Tj ET"
$objects = @(
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    "<< /Length $($stream.Length) >>`nstream`n$stream`nendstream",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
)
$pdf = [Text.StringBuilder]::new("%PDF-1.4`n")
$offsets = @()
for ($i = 0; $i -lt $objects.Count; $i++) {
    $offsets += $pdf.Length
    [void]$pdf.Append("$($i + 1) 0 obj`n$($objects[$i])`nendobj`n")
}
$xrefAt = $pdf.Length
[void]$pdf.Append("xref`n0 $($objects.Count + 1)`n0000000000 65535 f `n")
foreach ($offset in $offsets) { [void]$pdf.Append(('{0:D10} 00000 n `n' -f $offset)) }
[void]$pdf.Append("trailer`n<< /Size $($objects.Count + 1) /Root 1 0 R >>`nstartxref`n$xrefAt`n%%EOF`n")
$demoDir = Join-Path (Get-Location) "demo_files"
[IO.File]::WriteAllText((Join-Path $demoDir "sample_scan.pdf"), $pdf.ToString(), [Text.Encoding]::ASCII)
[IO.File]::WriteAllText((Join-Path $demoDir "renamed_text_file.pdf"),
    "This is a plain text file renamed to .pdf. The upload check reads the first bytes and rejects it.",
    [Text.Encoding]::ASCII)
Write-Ok "sample_scan.pdf (accepted) and renamed_text_file.pdf (rejected)"

# --- 6. Health checks -----------------------------------------------------------------
Write-Step "Health checks"
if ((Test-Url "$baseUrl/static/vendor/bootstrap/bootstrap.min.css") -ne 200) { throw "The stylesheet did not load from $baseUrl/static/." }
Write-Ok "Stylesheet loads"
Invoke-Manage check | Out-Null
Write-Ok "Django system check passed"
Invoke-Manage verify_audit_chain | Out-Null
Write-Ok "Audit hash chain verifies"

# --- 7. Optional: phone on the same Wi-Fi ---------------------------------------------
if ($Lan) {
    Write-Step "Opening the demo from a phone on the same Wi-Fi"
    $ips = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback|WSL' } |
        Select-Object -ExpandProperty IPAddress
    $lanReady = $envText -match '(?m)^\s*WEB_BIND\s*=\s*0\.0\.0\.0\s*$'
    foreach ($ip in $ips) {
        if ($lanReady -and $envText -match "(?m)^\s*DJANGO_ALLOWED_HOSTS\s*=.*\b$([regex]::Escape($ip))\b") {
            Write-Ok "Phones on this network can open http://${ip}:$port"
        } else {
            Write-Warn "To use http://${ip}:$port from a phone, set in .env:"
            Write-Host  "        WEB_BIND=0.0.0.0"
            Write-Host  "        DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,$ip"
            Write-Host  "    then run: docker compose up -d"
        }
    }
    Write-Warn "This is plain HTTP: use demo accounts and fake data only, never a real password."
}

Write-Step "Ready"
Write-Host "    Open $baseUrl and follow docs\DEMO.md."
Write-Host "    Use a separate private window (or browser profile) for each demo account."
Write-Host "    Locked out after 5 wrong passwords? Run:"
Write-Host "        docker compose run --rm manage python manage.py axes_reset"
