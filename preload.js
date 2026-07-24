const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getRepoInfo: () => ({ name: 'FlipFlow Repository' })
});
