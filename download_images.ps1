# download_images.ps1
# Downloads page images for the 10 export deeds from GCS into <DestRoot>\<deed>\
# Auto-detects which top-level prefix holds batch-1 by probing the first deed.
#
# Prereqs:
#   gcloud auth login aanya@sarvam.ai        (your ADC has list access; reader SA doesn't)
#   gcloud config set project vision-projects-463307
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File download_images.ps1 `
#       -ExportJson "C:\Users\aanya\Downloads\deed-validator\vertex_10_corrected.json" `
#       -DestRoot   "C:\Users\aanya\Downloads\deed-validator\data\vertex_batch\images"

param(
    [Parameter(Mandatory=$true)][string]$ExportJson,
    [Parameter(Mandatory=$true)][string]$DestRoot,
    [string]$Bucket = "vision-vertex-batch-asia-south1"
)

# Candidate top-level prefixes that might hold batch-1 (edit/reorder if you know it).
$Candidates = @("batch_inputs", "inputs", "inputs_batch1", "inputs_2002", "grounding")

$deeds = (Get-Content $ExportJson -Raw | ConvertFrom-Json).deed_number | ForEach-Object { "$_" }
Write-Host "Deeds to fetch:" ($deeds -join ", ")

# --- detect the prefix using the first deed ---
$firstDeed = $deeds[0]
$Prefix = $null
foreach ($c in $Candidates) {
    $probe = "gs://$Bucket/$c/grounding/images/$firstDeed/"
    Write-Host "Probing $probe ..."
    $out = & gcloud storage ls $probe 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { $Prefix = $c; break }
}
# Fallback: recursive search for the deed's images folder anywhere in the bucket
if (-not $Prefix) {
    Write-Host "No candidate matched; doing a recursive search (slower)..."
    $hit = & gcloud storage ls -r "gs://$Bucket/**/images/$firstDeed/page_001.png" 2>$null | Select-Object -First 1
    if ($hit) {
        # hit looks like gs://bucket/<prefix>/grounding/images/<deed>/page_001.png
        $Prefix = ($hit -replace "gs://$Bucket/","") -replace "/grounding/images/.*$",""
    }
}
if (-not $Prefix) {
    Write-Error "Could not locate images for $firstDeed. Run:  gcloud storage ls gs://$Bucket/  and tell me the prefixes."
    exit 1
}
Write-Host "Detected prefix: $Prefix" -ForegroundColor Green

# --- download each deed ---
foreach ($d in $deeds) {
    $src  = "gs://$Bucket/$Prefix/grounding/images/$d/"
    $dest = Join-Path $DestRoot $d
    New-Item -ItemType Directory -Force -Path $dest | Out-Null   # Windows needs the dir pre-created
    Write-Host "cp $src -> $dest"
    & gcloud storage cp "$src*.png" $dest 2>$null
    if ($LASTEXITCODE -ne 0) { & gcloud storage cp "$src*.jpg" $dest 2>$null }
    $n = (Get-ChildItem $dest -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "  $d : $n image(s)"
}
Write-Host "`nDone. Images under $DestRoot\<deed_number>\" -ForegroundColor Green
Write-Host "Use this DestRoot as --images-root for run_prompt_v2.py"
