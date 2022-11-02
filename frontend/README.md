Addon React frontend
====================

This is an addon frontend template. It contains all common UI components
the main frontend uses. Addon-specific logic is contained in `/src/addon`
directory, which must export a React component wrapped in `<main>` tag.

AddonWrapper provided by `/src/main.jsx` renders this component and pushes
the following props: 

 - projectName
 - addonName
 - addonVersion
 - context
 - accessToken

You don't need to use accessToken directly in your addon component, 
because all axios requests are already set up to use it.

**Warning:** Included components and stylesheets will change eventually!

