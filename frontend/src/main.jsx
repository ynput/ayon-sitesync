import React from 'react'
import ReactDOM from 'react-dom/client'
import Addon from './addon'

import axios from 'axios'

import 'primereact/resources/primereact.min.css'
import 'primeicons/primeicons.css'
import 'openpype-components/dist/style.css'
import './index.sass'


const AddonWrapper = () => {
  const [context, setContext] = React.useState(null)
  const [addonName, setAddonName] = React.useState(null)
  const [addonVersion, setAddonVersion] = React.useState(null)
  const [accessToken, setAccessToken] = React.useState(null)
  const [projectName, setProjectName] = React.useState(null)

  const processMessage = (e) => {

    const context = e.data.context
    setContext(context)
    setAddonName(e.data.addonName)
    setAddonVersion(e.data.addonVersion)
    setAccessToken(e.data.accessToken)
    setProjectName(context.projectName)

    if (e.data.accessToken) {
      axios.defaults.headers.common['Authorization'] = `Bearer ${e.data.accessToken}`
    }
  }

  React.useEffect(() => {
    window.addEventListener('message', processMessage, false)
  }, [])


  if (!accessToken || !projectName || !addonName || !addonVersion) {
    return <div>Initializing</div>
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
    <AddonWrapper />
  </React.StrictMode>
)
