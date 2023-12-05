import { useState, useEffect } from 'react'
import axios from 'axios'

import SiteSyncSummary from './summary'


const SiteSyncPage = ({projectName, addonName, addonVersion}) => {
  const [loading, setLoading] = useState(false)
  const [loadingUserSites, setLoadingUserSites] = useState(false)
  const [localSites, setLocalSites] = useState()
  const [remoteSites, setRemoteSites] = useState()
  const [totalCount, setTotalCount] = useState(0)
  const [repreNames, setRepreNames] = useState([])

  useEffect(() => {
    if (!projectName || !addonName || !addonVersion)
      return

    setLoading(true)
    setLoadingUserSites(true)

    const user_url = `/api/addons/${addonName}/${addonVersion}/${projectName}/get_user_sites`
    axios
      .get(user_url)
      .then((response) => {
        let local_sites = []
        for (const site_name of response.data["active_site"]){
            local_sites.push({ name: site_name, value: site_name })
        }
        setLocalSites(local_sites)
        
        let remote_sites = []
        for (const site_name of response.data["remote_site"]){
            remote_sites.push({ name: site_name, value: site_name })
        }
        setRemoteSites(remote_sites)
      })
      .finally(() => {
        setLoadingUserSites(false)
      })

    const url = `/api/addons/${addonName}/${addonVersion}/${projectName}/params`
    axios
      .get(url)
      .then((response) => {
        let rnames = []
        for (const name of response.data.names) {
          rnames.push({ name: name, value: name })
        }
        setTotalCount(response.data.count)
        setRepreNames(rnames)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [projectName])

  if (loading || loadingUserSites)
    return null

  if (!localSites || !remoteSites)
    return null

  return (
      <SiteSyncSummary
        addonName={addonName}
        addonVersion={addonVersion}
        projectName={projectName}
        localSites={localSites}
        remoteSites={remoteSites}
        names={repreNames}
        totalCount={totalCount}
      />
  )
}

export default SiteSyncPage
