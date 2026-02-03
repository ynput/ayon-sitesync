import React, { useContext, useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import Addon from './addon'
import { AddonProvider, AddonContext } from '@ynput/ayon-react-addon-provider'
import axios from 'axios'

import 'primereact/resources/primereact.min.css'
import 'primeicons/primeicons.css'
import '@ynput/ayon-react-components/dist/style.css'
import './index.sass'

const AddonWrapper = () => {
  const context = useContext(AddonContext)
  // const addonName = useContext(AddonContext).addonName
  const addonName = 'sitesync'
  // const addonVersion = useContext(AddonContext).addonVersion
  const addonVersion = '1.2.6'
  const accessToken = useContext(AddonContext).accessToken
  const projectName = useContext(AddonContext).projectName
  const userName = useContext(AddonContext).userName
  const [tokenSet, setTokenSet] = useState(false)

  useEffect(() => {
    if (accessToken && !tokenSet) {
      axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`
      setTokenSet(true)
    }
  }, [accessToken, tokenSet])

  if (!(tokenSet && projectName && userName)) {
    return null
  }

  return (
    <Addon
      context={context}
      addonName={addonName}
      addonVersion={addonVersion}
      accessToken={accessToken}
      projectName={projectName}
    />
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AddonProvider debug>
      <AddonWrapper />
    </AddonProvider>
  </React.StrictMode>,
)
