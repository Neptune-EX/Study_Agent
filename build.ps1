param(
    [string]$DistPath = (Join-Path $PSScriptRoot 'dist'),
    [string]$TeXLivePath = (Join-Path $PSScriptRoot 'texlive'),
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$spec = Join-Path $project 'MathMistakeBook.spec'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Missing .venv\Scripts\python.exe. Create the Windows virtual environment and install requirements.txt as shown in README.md.'
}
if (-not (Test-Path -LiteralPath $spec -PathType Leaf)) {
    throw 'Missing MathMistakeBook.spec. Restore the project spec file before building.'
}

$dist = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DistPath)
$release = Join-Path $dist 'MathMistakeBook'
$tex = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TeXLivePath)
$releasePrefix = $release.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$texPrefix = $tex.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($texPrefix.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $releasePrefix.StartsWith($texPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use separate, non-nested directories for the TeX Live source and the release.'
}

foreach ($entry in @('bin\windows\xelatex.exe', 'bin\windows\xdvipdfmx.exe', 'texmf-dist\tex\latex\ctex\ctexart.cls',
                     'texmf-dist\tex\latex\tcolorbox\tcolorbox.sty', 'texmf-dist\tex\latex\pgf\frontendlayer\tikz.sty')) {
    if (-not (Test-Path -LiteralPath (Join-Path $tex $entry) -PathType Leaf)) {
        throw "TeX Live is missing $entry under '$tex'. Supply the Windows installation tree in .\texlive or use -TeXLivePath."
    }
}

Push-Location -LiteralPath $project
try {
    & $python -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller is unavailable. Install requirements.txt in the project .venv.'
    }
    if ($CheckOnly) {
        Write-Host "Found the build spec, PyInstaller, and the required TeX Live files under '$tex'."
        return
    }

    & $python -m PyInstaller --clean --noconfirm --distpath $dist $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    Copy-Item -LiteralPath $tex -Destination (Join-Path $release 'texlive') -Recurse -Force
    Get-Item -LiteralPath (Join-Path $release 'MathMistakeBook.exe')
} finally {
    Pop-Location
}
