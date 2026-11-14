param([string]$DistPath = (Join-Path $PSScriptRoot 'dist'))

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$tex = Join-Path $project 'texlive'
$xelatex = Join-Path $tex 'bin\windows\xelatex.exe'

if (-not (Test-Path -LiteralPath $xelatex)) {
    throw 'Missing texlive/bin/windows/xelatex.exe. Please place the complete Windows TeX Live directory here first.'
}

Push-Location -LiteralPath $project
try {
    # Generate the spec file dynamically if needed, or use your existing spec
    & $python -m PyInstaller --clean --noconfirm --distpath $DistPath MathMistakeBook.spec
    if ($LASTEXITCODE -ne 0) { 
        throw "PyInstaller failed with exit code $LASTEXITCODE" 
    }
    
    $release = Join-Path $DistPath 'MathMistakeBook'
    
    # If texlive is NOT packed via --add-data in the spec, copy it here
    Copy-Item -LiteralPath $tex -Destination $release -Recurse -Force
    
    Get-Item -LiteralPath (Join-Path $release 'MathMistakeBook.exe')
} finally {
    Pop-Location
}