$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m unittest discover -s tests

if (Test-Path -Path ".\build") {
    Remove-Item -Path ".\build" -Recurse -Force
}
if (Test-Path -Path ".\dist\disco-virb-converter.exe") {
    Remove-Item -Path ".\dist\disco-virb-converter.exe" -Force
}

python -m PyInstaller `
    --onefile `
    --clean `
    --name disco-virb-converter `
    --collect-all garmin_fit_sdk `
    .\disco_virb_converter\__main__.py

Write-Host "Built .\dist\disco-virb-converter.exe"
