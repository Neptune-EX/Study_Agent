#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
python_bin="${PYTHON_BIN:-python3}"
dist="$PWD/dist/macos"
app="$dist/MathMistakeBook.app"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Keep TeX Live's engines, macros, fonts, formats and file indexes in a relocatable tree.
repository=https://mirror.ctan.org/systems/texlive/tlnet
curl --fail --location --retry 3 "$repository/install-tl-unx.tar.gz" -o "$work/install-tl.tar.gz"
tar -xzf "$work/install-tl.tar.gz" -C "$work"
tex="$work/texlive"
/usr/bin/perl "$work"/install-tl-*/install-tl --repository "$repository" \
    --no-interaction --no-continue --portable --texdir "$tex" --scheme=basic \
    --no-doc-install --no-src-install

tex_bin="$tex/bin/universal-darwin"
PATH="$tex_bin:$PATH" "$tex_bin/tlmgr" install \
    xetex amsmath ctex xecjk fandol fontspec zhnumber \
    geometry fancyhdr xcolor graphics pgf tcolorbox pdfcol environ etoolbox tools

# An explicit fontset in a document can override this bundled default.
mkdir -p "$tex/texmf-config/tex/latex/ctex"
printf '%s\n' '\ctex_set:nn { option } { fontset = fandol }' \
    > "$tex/texmf-config/tex/latex/ctex/ctexopts.cfg"
PATH="$tex_bin:$PATH" "$tex_bin/mktexlsr"
PATH="$tex_bin:$PATH" "$tex_bin/fmtutil-sys" --byfmt xelatex
test -x "$tex_bin/xdvipdfmx"
du -sh "$tex"

"$python_bin" -m PyInstaller --clean --noconfirm --distpath "$dist" MathMistakeBook.spec
mv "$tex" "$app/Contents/Resources/texlive"
# Refresh the ad-hoc signature after adding the TeX runtime.
/usr/bin/codesign --force --sign - "$app"
/usr/bin/codesign --verify --deep --strict "$app"

"$python_bin" -m scripts.check_macos_app "$app"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app" "$dist/MathMistakeBook-macOS.zip"
