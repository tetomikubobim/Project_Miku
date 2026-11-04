// 일정 관리 창 전용 preload: 일정 목록/추가/수정/삭제만 열어 준다.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('scheduleApi', {
  list: () => ipcRenderer.invoke('schedule:list'),
  add: (item) => ipcRenderer.invoke('schedule:add', item),
  update: (id, patch) => ipcRenderer.invoke('schedule:update', id, patch),
  remove: (id) => ipcRenderer.invoke('schedule:remove', id),
  onChanged: (cb) => ipcRenderer.on('schedule:changed', () => cb()),
});
