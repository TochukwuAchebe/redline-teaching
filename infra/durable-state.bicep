// Shared state for Redline: PostgreSQL metadata and queue. Deploy into Redline's own resource group.
param location string = 'uksouth'
@description('Globally unique PostgreSQL server name')
param serverName string
@secure()
param adminPassword string
param outboundIps array

resource databaseServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: 'redline_admin'
    administratorLoginPassword: adminPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      passwordAuth: 'Enabled'
      activeDirectoryAuth: 'Disabled'
    }
  }
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: databaseServer
  name: 'redline'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}
resource firewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = [for (ip, index) in outboundIps: {
  parent: databaseServer
  name: 'redline-egress-${index}'
  properties: {
    startIpAddress: ip
    endIpAddress: ip
  }
}]
output host string = databaseServer.properties.fullyQualifiedDomainName
