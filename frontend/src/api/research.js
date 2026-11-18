import {request,post} from './client.js';
export const studies={list:()=>request('/studies'),create:x=>post('/studies',x),rows:id=>request(`/studies/${id}/observations`),save:(id,x,row)=>row?request(`/studies/${id}/observations/${row}`,{method:'PUT',body:JSON.stringify(x)}):post(`/studies/${id}/observations`,x),remove:(id,row)=>request(`/studies/${id}/observations/${row}`,{method:'DELETE'})};
export const runs={list:()=>request('/runs'),create:x=>post('/runs',x),get:id=>request(`/runs/${id}`),review:(id,note)=>post(`/runs/${id}/review`,{note}),export:id=>request(`/runs/${id}/export`)};
export const methods={list:()=>request('/methods'),switch:code=>post('/methods/switch',{code})};
export const migrations={list:()=>request('/migrations'),create:target_method=>post('/migrations',{target_method}),get:id=>request(`/migrations/${id}`),switch:id=>post(`/migrations/${id}/switch`,{}),rollback:id=>post(`/migrations/${id}/rollback`,{}),abandon:id=>post(`/migrations/${id}/abandon`,{})};
