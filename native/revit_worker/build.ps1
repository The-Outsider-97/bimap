[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$RuntimeIdentifier = "win-x64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BrokerProject = Join-Path $Root "Bimap.RevitWorker.Broker\Bimap.RevitWorker.Broker.csproj"
$AppProject = Join-Path $Root "Bimap.RevitWorker.AppBundle\Bimap.RevitWorker.AppBundle.csproj"
$AppRoot = Join-Path $Root "Bimap.RevitWorker.AppBundle"
$Dist = Join-Path $Root "dist"
$BrokerDist = Join-Path $Dist "broker"
$AppPublish = Join-Path $Dist "appbundle-publish"
$BundleDir = Join-Path $Dist "BimapRevitWorker.bundle"
$BundleContents = Join-Path $BundleDir "Contents"
$BundleZip = Join-Path $Dist "BimapRevitWorker.AppBundle.zip"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET SDK 8+ is required to build the BIMAP Revit worker."
}

Remove-Item -Recurse -Force $Dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $BrokerDist -Force | Out-Null
New-Item -ItemType Directory -Path $AppPublish -Force | Out-Null
New-Item -ItemType Directory -Path $BundleContents -Force | Out-Null

Write-Host "[BIMAP] Restoring and building Revit AppBundle..."
dotnet restore $AppProject
dotnet publish $AppProject `
    -c $Configuration `
    --no-restore `
    -o $AppPublish

$AppDll = Join-Path $AppPublish "Bimap.RevitWorker.AppBundle.dll"
if (-not (Test-Path $AppDll)) {
    throw "AppBundle build completed without Bimap.RevitWorker.AppBundle.dll."
}

Copy-Item (Join-Path $AppRoot "PackageContents.xml") (Join-Path $BundleDir "PackageContents.xml")
Copy-Item (Join-Path $AppRoot "Bimap.RevitWorker.AppBundle.addin") (Join-Path $BundleContents "Bimap.RevitWorker.AppBundle.addin")
Copy-Item $AppDll (Join-Path $BundleContents "Bimap.RevitWorker.AppBundle.dll")

$XmlDoc = [xml](Get-Content -Raw (Join-Path $BundleDir "PackageContents.xml"))
if ($null -eq $XmlDoc.ApplicationPackage) {
    throw "Packaged AppBundle PackageContents.xml is invalid."
}

if (Test-Path $BundleZip) {
    Remove-Item -Force $BundleZip
}
Compress-Archive -Path $BundleDir -DestinationPath $BundleZip -CompressionLevel Optimal

Write-Host "[BIMAP] Publishing standalone Windows broker..."
dotnet restore $BrokerProject
dotnet publish $BrokerProject `
    -c $Configuration `
    -r $RuntimeIdentifier `
    --self-contained true `
    --no-restore `
    -p:PublishSingleFile=true `
    -p:PublishTrimmed=false `
    -o $BrokerDist

$BrokerExe = Join-Path $BrokerDist "Bimap.RevitWorker.Broker.exe"
if (-not (Test-Path $BrokerExe)) {
    throw "Broker publish completed without Bimap.RevitWorker.Broker.exe."
}

Write-Host ""
Write-Host "BIMAP Revit worker build completed."
Write-Host "Broker:    $BrokerExe"
Write-Host "AppBundle: $BundleZip"
Write-Host ""
Write-Host "Set BIMAP_REVIT_EXTRACTOR_EXECUTABLE to the Broker path after the APS AppBundle/Activity are provisioned."
