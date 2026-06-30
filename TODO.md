diagnose export times (manually seeks filenames instead of cached index/uasset path?)
test pak override order, maybe write a test that checks cases where two patches override the same base asset to ensure latest patch's item is used
preview thumbnails sometimes infinitely load until revisit/triggered refresh
triggered refresh resets edited asset thumbnails to original
keep reset data button updated during expansion

### regular
Menu: Refresh View, Show in Explorer, Back to Projects, Help/Info (https://github.com/clownfetus/Atelier#usage)
pressing save should close the material/vfx editor popup
force focus on webview window when confirmed started
confirmation before export files override files with same name
descriptive spinner text during initial (extra long loading for index)
replace on-boot win11 toast with extremely fast lightweight splash screen
sidebar: path under material/vfx items dont show anything, its just '/', show path truncated instead
hovering over item in sidebar should show tooltip with pak name + full path (cache index)
use webview max compatibility gui method, ex leave undefined if better compat
toggle select/deselect all when clicking the sidebar's circled number

### partially formed ideas
better filetype classification system?
allow multiple copies of texture files in sidebar, export only allows one of each texture to be exported at a time (still developing this idea, in the form of a completely optional advanced mode with mod profiles)
im pretty sure theres more that im forgetting rn