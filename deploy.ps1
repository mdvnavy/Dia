# Get configuration variables
$Region = $env:REGION
if (-not $Region) { $Region = "us-central1" }

$RepoName = $env:REPO_NAME
if (-not $RepoName) { $RepoName = "client-discovery-repo" }

$ServiceName = $env:SERVICE_NAME
if (-not $ServiceName) { $ServiceName = "client-discovery-agent-adk" }

$ProjectId = $env:PROJECT_ID
if (-not $ProjectId) {
    $ProjectId = gcloud config get-value project 2>$null
    if ($ProjectId) {
        $ProjectId = $ProjectId.Trim()
    }
}

if (-not $ProjectId -or $ProjectId -eq "(unset)" -or $ProjectId -eq "") {
    Write-Error "Error: PROJECT_ID is not set and could not be retrieved from gcloud config (it is empty or '(unset)')."
    Write-Host "Please set your project ID in your environment:" -ForegroundColor Yellow
    Write-Host "  `$env:PROJECT_ID='your-project-id'" -ForegroundColor Yellow
    Write-Host "Or configure gcloud:" -ForegroundColor Yellow
    Write-Host "  gcloud config set project your-project-id" -ForegroundColor Yellow
    exit 1
}

$ImageTag = "${Region}-docker.pkg.dev/${ProjectId}/${RepoName}/${ServiceName}:latest"

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Deploying ${ServiceName} to Google Cloud Run (Windows/PowerShell)"
Write-Host "Project ID:  ${ProjectId}"
Write-Host "Region:      ${Region}"
Write-Host "Repository:  ${RepoName}"
Write-Host "Image Tag:   ${ImageTag}"
Write-Host "========================================================" -ForegroundColor Cyan

Write-Host "Step 1: Enabling required GCP services..."
gcloud services enable `
  --project $ProjectId `
  artifactregistry.googleapis.com `
  run.googleapis.com `
  cloudbuild.googleapis.com

Write-Host "Step 2: Checking if Artifact Registry repository exists..."
$repoCheck = gcloud artifacts repositories describe $RepoName --location=$Region --project $ProjectId 2>&1
if ($repoCheck -match "NOT_FOUND" -or $LastExitCode -ne 0) {
    Write-Host "Creating Artifact Registry repository '${RepoName}'..."
    gcloud artifacts repositories create $RepoName `
      --project $ProjectId `
      --repository-format=docker `
      --location=$Region `
      --description="Docker repository for DIA web application"
} else {
    Write-Host "Repository '${RepoName}' already exists."
}

Write-Host "Step 3: Building and pushing Docker image using Cloud Build..."
gcloud builds submit --tag $ImageTag --project $ProjectId .

Write-Host "Step 4: Deploying to Cloud Run..."
gcloud run deploy $ServiceName `
  --project $ProjectId `
  --image $ImageTag `
  --region $Region `
  --platform managed `
  --allow-unauthenticated `
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${ProjectId},GOOGLE_CLOUD_LOCATION=${Region}"

Write-Host "========================================================" -ForegroundColor Green
$ServiceUrl = gcloud run services describe $ServiceName --region $Region --project $ProjectId --format='value(status.url)'
Write-Host "Deployment completed successfully!" -ForegroundColor Green
Write-Host "Service URL: $ServiceUrl" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
