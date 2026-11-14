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
cat > "$tex/texmf-config/tex/latex/ctex/ctexopts.cfg" <<'EOF'
\ProvidesExplFile{ctexopts.cfg}{}{}{MathMistakeBook bundled font defaults}
\ctex_set:nn { option } { fontset = fandol }
EOF
PATH="$tex_bin:$PATH" "$tex_bin/mktexlsr"
PATH="$tex_bin:$PATH" "$tex_bin/fmtutil-sys" --byfmt xelatex
du -sh "$tex"

# Install real files for the two entry points. In TeX Live xelatex is
# usually a symlink to xetex, but XeTeX needs the xelatex name for its format.
mkdir -p "$tex/bin/bundle"
for tool in xelatex xdvipdfmx; do
    /usr/bin/install -m 755 "$tex_bin/$tool" "$tex/bin/bundle/$tool"
done
MMB_TEXLIVE_DIR="$tex" PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=1 "$python_bin" -m PyInstaller \
    --clean --noconfirm --distpath "$dist" MathMistakeBook.spec
/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

"$python_bin" -m scripts.check_macos_app "$app"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app" "$dist/MathMistakeBook-macOS.zip"
