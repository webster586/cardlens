#Requires -Version 5.1

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

}


Pop-Location