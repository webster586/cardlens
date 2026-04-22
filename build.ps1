#Requires -Version 5.1
param([switch]$Installer)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot

$python = ".venv\Scripts\python.exe"
$pip    = ".venv\Scripts\pip.exe"
$pyi    = ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $python)) {
    Write-Error "venv not found. Run scripts\bootstrap_env.ps1 first."
    exit 1
}

if (-not (Test-Path $pyi)) {
    Write-Host "PyInstaller not found - installing..." -ForegroundColor Yellow
    & $pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller install failed."; exit 1 }
}

$modelDir = "$env:USERPROFILE\.EasyOCR\model"
$required = @("craft_mlt_25k.pth", "latin_g2.pth")
$missingModels = @()

foreach ($m in $required) {
    if (-not (Test-Path "$modelDir\$m")) { $missingModels += $m }
}

if ($missingModels.Count -gt 0) {
    Write-Warning "Missing EasyOCR model(s) at $modelDir :"
    $missingModels | ForEach-Object { Write-Warning "  - $_" }
    Write-Warning "Start the app once with internet access to download them, then rebuild."
    Write-Warning "Continuing build WITHOUT bundled models - OCR will not work in the release."
} else {
    $allModels = Get-ChildItem $modelDir -File
    $totalMB   = [math]::Round(($allModels | Measure-Object Length -Sum).Sum / 1MB, 0)
    Write-Host "EasyOCR models OK - $($allModels.Count) file(s), ${totalMB} MB" -ForegroundColor Green
}

$logoCount = (Get-ChildItem "data\catalog_images\logo_*.png" -ErrorAction SilentlyContinue).Count
if ($logoCount -eq 0) {
    Write-Warning "No logo_*.png files found in data\catalog_images\ - set logos will not be bundled."
} else {
    Write-Host "Set logos OK - $logoCount file(s) will be bundled." -ForegroundColor Green
}

Write-Host ""
Write-Host "Running PyInstaller..." -ForegroundColor Cyan

& $pyi pokemon_scanner.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

$outDir = "dist\CardLens"
if (Test-Path $outDir) {
    $sizeMB = [math]::Round(
        (Get-ChildItem $outDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0
    )
    Write-Host ""
    Write-Host "Build successful!" -ForegroundColor Green
    Write-Host "  Output  : $(Resolve-Path $outDir)"
    Write-Host "  Size    : ~${sizeMB} MB"
    Write-Host "  EXE     : $outDir\CardLens.exe"
    Write-Host ""

    if ($Installer) {
        Write-Host "Compiling Inno Setup installer..." -ForegroundColor Cyan
        $iscc = $null
        foreach ($candidate in @(
            "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            "C:\Program Files\Inno Setup 6\ISCC.exe"
        )) {
            if (Test-Path $candidate) { $iscc = $candidate; break }
        }
        if (-not $iscc) {
            $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($found) { $iscc = $found.Source }
        }
        if (-not $iscc) {
            Write-Warning "Inno Setup 6 not found. Download from https://jrsoftware.org/isinfo.php"
            Write-Warning "Then re-run: .\build.ps1 -Installer"
        } else {
            & $iscc "installer\pokemon_scanner.iss"
            if ($LASTEXITCODE -eq 0) {
                $setupExe = "installer\Output\PokemonScannerSetup.exe"
                if (Test-Path $setupExe) {
                    $setupMB = [math]::Round((Get-Item $setupExe).Length / 1MB, 0)
                    Write-Host "Installer built!" -ForegroundColor Green
                    Write-Host "  Setup   : $(Resolve-Path $setupExe)"
                    Write-Host "  Size    : ~${setupMB} MB"
                }
            } else {
                Write-Error "Inno Setup compilation failed."
            }
        }
    } else {
        Write-Host "Next: run  .\build.ps1 -Installer  to compile the Inno Setup installer." -ForegroundColor Cyan
    }
}

Pop-Location