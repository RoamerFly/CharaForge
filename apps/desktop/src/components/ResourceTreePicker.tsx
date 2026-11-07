import { useMemo, useRef, useState, type ChangeEvent } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { Search, X, Maximize2 } from "lucide-react";
import { ImagePreviewDialog } from "./ImagePreviewDialog";

export type TreeResource={id:string; name:string; groups:string[]; image?:string; description:string; disabled?:boolean};
type Node={name:string; key:string; children:Node[]; leaves:TreeResource[]};
function tree(items:TreeResource[]):Node {
  const root:Node={name:"全部",key:"root",children:[],leaves:[]};
  for(const item of items){let current=root;for(const group of item.groups){let child=current.children.find(n=>n.name===group);if(!child){child={name:group,key:`${current.key}/${group}`,children:[],leaves:[]};current.children.push(child);}current=child;}current.leaves.push(item);}
  return root;
}
const descendants=(node:Node):TreeResource[]=>[...node.leaves,...node.children.flatMap(descendants)];
export function ResourceTreePicker({title,items,selected,onChange,disabled}:{title:string;items:TreeResource[];selected:string[];onChange:(ids:string[])=>void;disabled:boolean}) {
  const dialog=useRef<HTMLDialogElement>(null);const [query,setQuery]=useState("");const [preview,setPreview]=useState<TreeResource|null>(null);
  const filtered=useMemo(()=>items.filter(i=>[i.name,...i.groups].join(" ").toLowerCase().includes(query.trim().toLowerCase())),[items,query]);
  const root=useMemo(()=>tree(filtered),[filtered]);
  const toggle=(ids:string[],checked:boolean)=>onChange(checked?[...new Set([...selected,...ids])]:selected.filter(i=>!ids.includes(i)));
  const branch=(node:Node)=>{const eligible=descendants(node).filter(i=>!i.disabled).map(i=>i.id);const count=eligible.filter(i=>selected.includes(i)).length;
    return <details key={node.key} open={Boolean(query)||node===root} className="resource-branch"><summary><input type="checkbox" aria-label={`选择${node.name}`} checked={eligible.length>0&&count===eligible.length} ref={el=>{if(el)el.indeterminate=count>0&&count<eligible.length;}} disabled={!eligible.length||disabled} onClick={e=>e.stopPropagation()} onChange={(e:ChangeEvent<HTMLInputElement>)=>toggle(eligible,e.target.checked)}/><span>{node.name}</span><small>{count}/{eligible.length}</small></summary>
      {node.children.map(branch)}{node.leaves.map(item=><div className="resource-leaf" key={item.id}><label><input type="checkbox" checked={selected.includes(item.id)} disabled={disabled||item.disabled} onChange={e=>toggle([item.id],e.target.checked)}/><span>{item.name}{item.disabled&&<small>缺少已采用参考</small>}</span></label>{item.image&&<button type="button" className="resource-thumb" title={`预览${item.name}`} onClick={()=>setPreview(item)}><img loading="lazy" src={convertFileSrc(item.image)} alt=""/><Maximize2 size={12}/></button>}<p>{item.description}</p></div>)}</details>;
  };
  return <><button type="button" className="ghost-button resource-picker-trigger" disabled={disabled} onClick={()=>dialog.current?.showModal()}>{title}<span>{selected.length?`已选 ${selected.length}`:"选择"}</span></button>
    <dialog ref={dialog} className="studio-dialog resource-tree-dialog" aria-label={title}><div className="studio-dialog-heading"><h2>{title}</h2><button type="button" className="icon-button" aria-label={`关闭${title}`} onClick={()=>dialog.current?.close()}><X size={18}/></button></div><label className="resource-search"><Search size={16}/><input type="search" aria-label={`搜索${title}`} value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索名称或分类"/></label><div className="resource-tree-body">{branch(root)}{!filtered.length&&<p>没有匹配项</p>}</div><div className="studio-dialog-actions"><button type="button" className="ghost-button" onClick={()=>onChange([])}>清空</button><button type="button" className="primary-button" onClick={()=>dialog.current?.close()}>完成（{selected.length}）</button></div></dialog>
    <ImagePreviewDialog source={preview?.image?convertFileSrc(preview.image):null} title={preview?.name??"参考图"} description={preview?.description} onClose={()=>setPreview(null)}/>
  </>;
}
