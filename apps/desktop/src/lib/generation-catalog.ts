import { composePrompt, type GenerationReference, type PromptParts } from "./cloud-generation";

export type VariantResource = { id: string; name: string; identity: string; outfit: string; quality: string; references: GenerationReference[]; issues: string[]; version: string };
export type PoseResource = { id: string; name: string; category: string; subcategory: string; prompt_zh: string; prompt_en: string; reference: GenerationReference | null; required_supports: string[]; status: string; version: string };
export type SceneResource = { id: string; name: string; category: string; group: string; prompt: string; capabilities: string[]; views: {id:string; direction:string; camera:string; reference:GenerationReference}[]; status: string; version: string };
export type GenerationCatalog = { root: string; characters: {id:string; name:string; variants:VariantResource[]}[]; poses:PoseResource[]; scenes:SceneResource[]; issues:string[] };
export type SelectionSpec = { poses:string[]; scenes:string[]; randomPose:boolean; randomScene:boolean; count:number; mode:"total"|"per_pose"|"combinations"; direction:string };
export type PlannedJob = { id:string; variant_id:string; variant_version:string; pose_id:string; scene_id:string; pose_name:string; scene_name:string; pose_category:string; scene_category:string; pose_version:string; scene_version:string; scene_direction:string; prompt_parts:PromptParts; prompt:string; references:GenerationReference[] };
export type PlanSelectionRecord = { selection: SelectionSpec; random_seed: number; random_algorithm: "mulberry32-v1" };

export function seededRandom(seed: number): () => number {
  return () => {
    let n = seed = (seed + 0x6d2b79f5) | 0;
    n = Math.imul(n ^ (n >>> 15), n | 1);
    n ^= n + Math.imul(n ^ (n >>> 7), n | 61);
    return ((n ^ (n >>> 14)) >>> 0) / 4294967296;
  };
}
export function mergeReferences(manual:GenerationReference[], automatic:GenerationReference[]): GenerationReference[] {
  const seen=new Set<string>();
  return [...automatic,...manual.filter(r=>!r.source)].filter(r=>{const key=r.path.replaceAll("\\","/").toLowerCase();if(seen.has(key))return false;seen.add(key);return true;});
}
export function compatible(pose:PoseResource,scene:SceneResource):boolean {
  return pose.required_supports.every(s=>scene.capabilities.includes(s));
}
export function sceneModule(scene:SceneResource,direction:string):{text:string;reference:GenerationReference} {
  const view=scene.views.find(v=>v.id===direction);
  if(!view)throw new Error(`${scene.name} 缺少所选方向参考`);
  return {text:[scene.prompt,view.camera,`相机朝向：${view.direction}。保持该方向布局，不镜像房间。`].join("\n"),reference:{...view.reference,source:"scene",resource_id:scene.id}};
}
export function planGeneration(catalog:GenerationCatalog,variant:VariantResource,spec:SelectionSpec,parts:PromptParts,manual:GenerationReference[],rng:()=>number=Math.random):PlannedJob[] {
  if(!Number.isSafeInteger(spec.count)||spec.count<1||spec.count>500)throw new Error("数量须为 1–500 的整数");
  if(variant.issues.length)throw new Error(variant.issues.join("；"));
  const poses=catalog.poses.filter(p=>spec.poses.includes(p.id));
  const scenes=catalog.scenes.filter(s=>spec.scenes.includes(s.id));
  if(poses.length!==new Set(spec.poses).size||scenes.length!==new Set(spec.scenes).size)throw new Error("所选资源已经变化，请刷新后重新选择");
  if(!poses.length||!scenes.length)throw new Error("请选择姿势和场景；随机模式也需选择候选范围");
  if(poses.some(p=>!p.reference))throw new Error("所选姿势仍缺少已采用的灰模参考图");
  const pairs=poses.flatMap(p=>scenes.filter(s=>compatible(p,s)&&s.views.some(v=>v.id===spec.direction)).map(s=>({p,s})));
  const eligiblePoses = poses.filter(pose => pairs.some(pair => pair.p.id === pose.id));
  if (!eligiblePoses.length) throw new Error("所选方向没有满足姿势支撑条件的场景，请调整候选范围");
  if (!spec.randomPose) {
    const missing = poses.find(pose => !eligiblePoses.includes(pose));
    if (missing) throw new Error(`${missing.name} 没有满足支撑条件的场景，请更换场景或核验支撑元数据`);
  }
  const choose=<T,>(items:T[]):T=>items[Math.min(items.length-1,Math.floor(Math.max(0,rng())*items.length))];
  const randomScene = (eligible: typeof pairs) => {
    const group = choose([...new Set(eligible.map(pair => pair.s.group))]);
    return choose(eligible.filter(pair => pair.s.group === group));
  };
  const total=spec.mode==="combinations"?pairs.length*spec.count:spec.mode==="per_pose"?poses.length*spec.count:spec.count;
  if(total>500)throw new Error(`展开后共 ${total} 张，单批最多 500 张`);
  let selected:typeof pairs=[];
  if(spec.mode==="combinations") { if(spec.randomPose||spec.randomScene)throw new Error("全部组合模式不使用随机开关"); selected=pairs.flatMap(pair=>Array.from({length:spec.count},()=>pair)); }
  else if(spec.mode==="per_pose") selected=poses.flatMap(p=>Array.from({length:spec.count},(_,i)=>{const eligible=pairs.filter(x=>x.p.id===p.id);return spec.randomScene?randomScene(eligible):eligible[i%eligible.length];}));
  else selected=Array.from({length:spec.count},(_,i)=>{
    const p=spec.randomPose?choose(eligiblePoses):poses[i%poses.length];const eligible=pairs.filter(x=>x.p.id===p.id);
    if (!spec.randomScene) return eligible[Math.floor(i / poses.length) % eligible.length];
    return randomScene(eligible);
  });
  if(selected.length>500)throw new Error(`展开后共 ${selected.length} 张，单批最多 500 张`);
  return selected.map(({p,s},i)=>{
    const scene=sceneModule(s,spec.direction);
    const references=mergeReferences(manual,[...variant.references.map(r=>({...r,source:"character" as const,resource_id:variant.id})),{...p.reference!,source:"pose",resource_id:p.id},scene.reference]);
    if(references.length>8)throw new Error(`${p.name} 的参考图超过 8 张，请减少手动附件`);
    const prompt_parts={...parts,identity:parts.identity.trim()||[variant.identity,variant.outfit].filter(Boolean).join("\n\n"),quality:parts.quality.trim()||variant.quality,pose:poses.length===1&&parts.pose.trim()?parts.pose:p.prompt_en||p.prompt_zh,scene:scenes.length===1&&parts.scene.trim()?parts.scene:scene.text};
    return {id:`job-${String(i+1).padStart(4,"0")}`,variant_id:variant.id,variant_version:variant.version,pose_id:p.id,scene_id:s.id,pose_name:p.name,scene_name:s.name,pose_category:p.category,scene_category:s.group,pose_version:p.version,scene_version:s.version,scene_direction:spec.direction,prompt_parts,prompt:composePrompt(prompt_parts,[]),references};
  });
}
