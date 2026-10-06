# Rebuilds the PyInstaller bootloader from source and installs PyInstaller with it into .venv.
# The stock bootloader is shared by every PyInstaller app (malware included), so some antiviruses flag it;
# a locally compiled one is the same code but a different binary.
# Needs Visual Studio Build Tools with C++:
#   winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
$ErrorActionPreference = "Stop"
$python = (Resolve-Path .venv\Scripts\python.exe).Path
$version = (& $python -c "import PyInstaller; print(PyInstaller.__version__)")
$work = Join-Path $env:TEMP "pyinstaller-src"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $work | Out-Null
& $python -m pip download "pyinstaller==$version" --no-binary :all: --no-deps -d $work
$ErrorActionPreference = "Continue"
tar -xzf (Join-Path $work "pyinstaller-$version.tar.gz") -C $work  # a few doc/test symlinks fail on Windows; not needed
$src = Join-Path $work "pyinstaller-$version"
Push-Location (Join-Path $src "bootloader")
& $python ./waf distclean all --target-arch=64bit
Pop-Location
& $python -m pip install --force-reinstall --no-deps $src
