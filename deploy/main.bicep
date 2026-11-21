// Demo hosting on Azure App Service, free tier only: one Linux F1 plan and one web app.
// Deploy:  az deployment group create -g <resource group> -f deploy/main.bicep
// then upload the built site with deploy/package.sh and `az webapp deploy` (see docs/architecture.md).

@description('Web app name (becomes <name>.azurewebsites.net)')
param appName string = 'maritime-satellite-pipeline'

@description('Azure region')
param location string = resourceGroup().location

@description('Reuse an existing Linux F1 plan instead of creating one (resource id), or leave empty')
param existingPlanId string = ''

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = if (empty(existingPlanId)) {
  name: '${appName}-plan'
  location: location
  kind: 'linux'
  sku: {
    name: 'F1'
    tier: 'Free'
    size: 'F1'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  properties: {
    serverFarmId: empty(existingPlanId) ? plan!.id : existingPlanId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'python -m uvicorn app:app --host 0.0.0.0 --port 8000'
      alwaysOn: false
      // the platform calls this every minute; it also flags an instance that stops answering
      healthCheckPath: '/api/health'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_PORT', value: '8000' }
      ]
    }
  }
}

output url string = 'https://${site.properties.defaultHostName}'
output sku string = empty(existingPlanId) ? plan!.sku.name : 'existing plan'
