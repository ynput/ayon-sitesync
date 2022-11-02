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

    // TODO: Use addon's own endpoint instead the deprecated one
    // so the url would be:
    //
    // `/api/addons/${addonName}/${addonVersion}/ .... `

    const url = `/api/projects/${projectName}/sitesync/params`
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
    return <main>Loading...</main>

  return (
    <main>
      <SiteSyncSummary
        projectName={projectName}
        localSite={localSite}
        remoteSite={remoteSite}
        names={repreNames}
        totalCount={totalCount}
      />
    </main>
  )
}

export default SiteSyncPage
