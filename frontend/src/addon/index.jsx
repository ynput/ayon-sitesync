import { useState, useEffect } from 'react'
import axios from 'axios'

import SiteSyncSummary from './summary'


const SiteSyncPage = ({projectName, addonName, addonVersion}) => {
  const localSite = 'local'
  const remoteSite = 'remote'

  const [loading, setLoading] = useState(false)
  const [totalCount, setTotalCount] = useState(0)
  const [repreNames, setRepreNames] = useState([])

  useEffect(() => {
    if (!projectName || !addonName || !addonVersion)
      return

    setLoading(true)

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

  if (loading)
    return null

  return (
      <SiteSyncSummary
        addonName={addonName}
        addonVersion={addonVersion}
        projectName={projectName}
        localSite={localSite}
        remoteSite={remoteSite}
        names={repreNames}
        totalCount={totalCount}
      />
  )
}

export default SiteSyncPage
