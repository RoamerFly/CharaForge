import { useEffect,useMemo,useState,type Dispatch,type SetStateAction } from "react";
import { invokeCore,isTauriRuntime } from "../lib/tauri";
import { mergeReferences,planGeneration,sceneModule,seededRandom,type GenerationCatalog,type PlannedJob,type SelectionSpec,type PlanSelectionRecord } from "../lib/generation-catalog";
import type { GenerationReference,PromptParts } from "../lib/cloud-generation";
import type { CharacterTarget } from "./CharacterWorkspacePanel";
import { ResourceTreePicker } from "./ResourceTreePicker";
import { useResourcePrompts, type PromptBindings } from "../lib/resource-prompts";
const INITIAL:SelectionSpec={poses:[],scenes:[],randomPose:false,randomScene:false,count:1,mode:"total",direction:"F"};
type Props={target:CharacterTarget|null;parts:PromptParts;setParts:Dispatch<SetStateAction<PromptParts>>;references:GenerationReference[];setReferences:Dispatch<SetStateAction<GenerationReference[]>>;disabled:boolean;onPlan:(jobs:PlannedJob[],variant:string,selection:PlanSelectionRecord)=>Promise<void>;onError:(message:string)=>void};
export function GenerationResourceControls(p:Props){
  const [catalog,setCatalog]=useState<GenerationCatalog|null>(null);const [variantId,setVariantId]=useState("");const [spec,setSpec]=useState<SelectionSpec>(INITIAL);const [loading,setLoading]=useState(false);const [saving,setSaving]=useState(false);const [revision,setRevision]=useState(0);
  const key=p.target?`${p.target.library}/${p.target.name}`:"";
  const character=catalog?.characters.find(c=>c.name===p.target?.name);const variant=character?.variants.find(v=>v.id===variantId)??character?.variants[0];
  useEffect(()=>{let active=true;setCatalog(null);setVariantId("");setSpec({...INITIAL});
    p.setReferences(items=>items.filter(r=>!r.source));
    if(!p.target||!isTauriRuntime())return;
    setLoading(true);void invokeCore<GenerationCatalog>("generation_catalog","generation.catalog",{characterTarget:p.target}).then(r=>{if(!active)return;if(r.payload)setCatalog(r.payload);else p.onError(r.error?.message??"资源库读取失败");}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[key,revision]);
  const selectedPose = catalog?.poses.find(pose => pose.id === spec.poses[0]);
  const selectedScene = catalog?.scenes.find(scene => scene.id === spec.scenes[0]);
  const sceneBinding = selectedScene?.views.some(view => view.id === spec.direction)
    ? sceneModule(selectedScene, spec.direction) : null;
  const bindings: PromptBindings = {
    identity: { key: `${key}/identity/${variant?.id ?? "loading"}`, text: variant ? [variant.identity, variant.outfit].filter(Boolean).join("\n\n") : "" },
    quality: { key: `${key}/quality/${variant?.id ?? "loading"}`, text: variant?.quality ?? "" },
    pose: { key: `${key}/pose/${selectedPose?.id ?? "manual"}`, text: selectedPose?.prompt_en || selectedPose?.prompt_zh || "" },
    scene: { key: `${key}/scene/${selectedScene?.id ?? "manual"}/${spec.direction}`, text: sceneBinding?.text ?? "" },
  };
  const promptEdits = useResourcePrompts(p.parts, p.setParts, bindings);
  useEffect(()=>{
    if(!catalog||!variant)return;const pose=catalog.poses.find(x=>x.id===spec.poses[0]);const scene=catalog.scenes.find(x=>x.id===spec.scenes[0]);
    let sceneReference:GenerationReference|undefined;
    if(scene){try{sceneReference=sceneModule(scene,spec.direction).reference;}catch(e){p.onError(String(e));}}
    p.setReferences(items=>mergeReferences(items,[...variant.references.map(r=>({...r,source:"character" as const,resource_id:variant.id})),...(pose?.reference?[{...pose.reference,source:"pose" as const,resource_id:pose.id}]:[]),...(sceneReference?[sceneReference]:[])]));
  },[catalog,variant?.id,spec.poses.join(","),spec.scenes.join(","),spec.direction]);
  const disabled=p.disabled||loading||saving;
  const preflight = useMemo(() => {
    if (!catalog || !variant || !spec.poses.length || !spec.scenes.length) return null;
    try {
      const jobs = planGeneration(catalog, variant, spec, p.parts, p.references, seededRandom(0));
      return { count: jobs.length, error: "" };
    } catch (error) {
      return { count: 0, error: error instanceof Error ? error.message : String(error) };
    }
  }, [catalog, variant, spec, p.parts, p.references]);
  const build=async()=>{
    if(!catalog||!variant||!character)return;
    setSaving(true);
    try {
      const random_seed = crypto.getRandomValues(new Uint32Array(1))[0];
      const selection: PlanSelectionRecord = { selection: spec, random_seed, random_algorithm: "mulberry32-v1" };
      const jobs = planGeneration(catalog,variant,spec,p.parts,p.references,seededRandom(random_seed));
      await p.onPlan(jobs,variant.id==="default"?character.name:`${character.name}-${variant.name}`,selection);
    } catch(e) {
      p.onError(e instanceof Error?e.message:String(e));
    } finally { setSaving(false); }
  };
  return <section className="standard-resources" aria-label="标准资源与批量任务"><div className="resource-section-heading"><strong>标准资源</strong><button type="button" className="ghost-button" disabled={disabled||!p.target} onClick={()=>setRevision(x=>x+1)}>{loading?"读取中…":"刷新"}</button></div>
    {!p.target?<small>先选择角色工坊库和角色。</small>:<>
      <label className="generation-field"><span>角色造型</span><select aria-label="角色造型" disabled={disabled||!character} value={variant?.id??""} onChange={e=>setVariantId(e.target.value)}>{character?.variants.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}{!character&&<option>暂无可用造型配置</option>}</select></label>
      {variant?.issues.map(issue=><p key={issue} role="alert" className="cloud-error">{issue}</p>)}
      {promptEdits.modified.length > 0 && <div className="resource-section-heading"><small>当前资源提示词已编辑</small><button type="button" className="ghost-button" disabled={disabled} onClick={promptEdits.reset}>恢复库版本</button></div>}
      <ResourceTreePicker title="姿势" disabled={disabled||!catalog} selected={spec.poses} onChange={poses=>setSpec(s=>({...s,poses}))} items={catalog?.poses.map(pose=>({id:pose.id,name:pose.name,groups:[pose.category,pose.subcategory],image:pose.reference?.path,description:pose.prompt_zh,disabled:!pose.reference}))??[]}/>
      <ResourceTreePicker title="场景" disabled={disabled||!catalog} selected={spec.scenes} onChange={scenes=>setSpec(s=>({...s,scenes}))} items={catalog?.scenes.map(scene=>({id:scene.id,name:scene.name,groups:[scene.category,scene.group],image:scene.views.find(v=>v.id===spec.direction)?.reference.path,description:scene.prompt}))??[]}/>
      <div className="resource-random-options"><label><input type="checkbox" disabled={disabled||spec.mode!=="total"} checked={spec.randomPose} onChange={e=>setSpec(s=>({...s,randomPose:e.target.checked,poses:s.poses.length?s.poses:catalog?.poses.filter(p=>p.reference).map(p=>p.id)??[]}))}/>随机姿势</label><label><input type="checkbox" disabled={disabled||spec.mode==="combinations"} checked={spec.randomScene} onChange={e=>setSpec(s=>({...s,randomScene:e.target.checked,scenes:s.scenes.length?s.scenes:catalog?.scenes.map(p=>p.id)??[]}))}/>随机场景</label></div>
      <div className="generation-grid"><label className="generation-field"><span>数量方式</span><select aria-label="数量方式" disabled={disabled} value={spec.mode} onChange={e=>setSpec(s=>({...s,mode:e.target.value as SelectionSpec["mode"],randomPose:false,randomScene:false}))}><option value="total">总张数</option><option value="per_pose">每个姿势</option><option value="combinations">每个兼容组合</option></select></label><label className="generation-field"><span>数量</span><input aria-label="数量" type="number" min={1} max={500} disabled={disabled} value={spec.count} onChange={e=>setSpec(s=>({...s,count:Number(e.target.value)}))}/></label><label className="generation-field"><span>场景方向</span><select aria-label="场景方向" disabled={disabled} value={spec.direction} onChange={e=>setSpec(s=>({...s,direction:e.target.value}))}>{[["F","前"],["B","后"],["L","左"],["R","右"]].map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label></div>
      {preflight?.error ? <p role="alert" className="cloud-error">{preflight.error}</p>
        : preflight && <small role="status">预计生成 {preflight.count} 张</small>}
      <button type="button" className="ghost-button" disabled={disabled||!preflight?.count} onClick={()=>void build()}>{saving?"保存快照…":"建立批次 · 暂不调用 API"}</button>
      {(spec.poses.length>1||spec.scenes.length>1)&&<small>下方显示首项；批次会分别填入每个动作和场景，并检查支撑条件。</small>}
      {catalog?.issues.length?<details className="studio-disclosure"><summary>资源提示（{catalog.issues.length}）</summary>{catalog.issues.map((issue,i)=><p key={i}>{issue}</p>)}</details>:null}
    </>}
  </section>;
}
