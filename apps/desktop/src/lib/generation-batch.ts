import { useCallback, useEffect, useRef, useState } from "react";
import { invokeCore, isTauriRuntime } from "./tauri";
import type { CharacterTarget } from "../components/CharacterWorkspacePanel";
import type { CloudResult, GenerationReference, PromptParts } from "./cloud-generation";
import type { PlanSelectionRecord, PlannedJob } from "./generation-catalog";
type Input = {id:string;pose_name:string;scene_name:string;pose_category:string;scene_category:string;request:{channel:string;prompt:string;prompt_parts:PlannedJob["prompt_parts"];size:string;quality:string;references:GenerationReference[]};resources:unknown};
export type BatchJob={input:Input;status:string;attempts:number;error:string|null;result:CloudResult|null;review:"pending"|"approved"|"repair";review_reason:string;promoted:string|null};
export type GenerationBatch={id:string;character:string;variant:string;created_at:string;jobs:BatchJob[]};
export const JOB_STATUS:Record<string,string>={queued:"未执行",running:"执行中",succeeded:"已返回",failed:"失败",uncertain:"结果未确认",cancelled:"已取消"};
export const REVIEW_STATUS={pending:"待审核",approved:"已通过",repair:"待返修"};
export function useGenerationBatch(target:CharacterTarget|null,onBusy:(busy:boolean)=>void,onError:(error:string)=>void){
  const [batches,setBatches]=useState<GenerationBatch[]>([]);const [activeId,setActiveId]=useState("");const [running,setRunning]=useState(false);
  const stop=useRef(false);const mounted=useRef(true);const context=useRef("");
  const key=target?`${target.library}/${target.name}`:"";context.current=key;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;stop.current=true;};},[]);
  const put=useCallback((batch:GenerationBatch)=>{setBatches(items=>[batch,...items.filter(b=>b.id!==batch.id)]);setActiveId(batch.id);},[]);
  const refresh=useCallback(async()=>{
    if(!target||!isTauriRuntime())return;const own=key;
    const r=await invokeCore<GenerationBatch[]>("generation_batch_list","generation.batch",{characterTarget:target});
    if(context.current!==own||!mounted.current)return;
    if(r.payload)setBatches(r.payload);else onError(r.error?.message??"无法读取批次");
  },[key,target,onError]);
  useEffect(()=>{setBatches([]);setActiveId("");stop.current=true;void refresh();},[key]);
  const create=async(jobs:PlannedJob[],variant:string,channel:string,size:string,quality:string,selection:PlanSelectionRecord)=>{
    if(!target||running)throw new Error("请先选择角色，或等待当前请求完成");
    const own=key;
    const r=await invokeCore<GenerationBatch>("generation_batch_create","generation.batch",{characterTarget:target,variant,selection,jobs:jobs.map(j=>({id:j.id,pose_name:j.pose_name,scene_name:j.scene_name,pose_category:j.pose_category,scene_category:j.scene_category,request:{channel,prompt:j.prompt,prompt_parts:j.prompt_parts,references:j.references,size,quality},resources:{character:target.name,variant_id:j.variant_id,variant_version:j.variant_version,pose_id:j.pose_id,scene_id:j.scene_id,pose_version:j.pose_version,scene_version:j.scene_version,scene_direction:j.scene_direction}}))});
    if(!r.payload)throw new Error(r.error?.message??"建立批次失败");if(context.current!==own||!mounted.current)throw new Error("角色已切换，批次保存在原角色目录；切回后可继续。");put(r.payload);return r.payload;
  };
  const run=async(batch:GenerationBatch)=>{
    if(!target||running)return;const own=key;const frozenTarget={...target};stop.current=false;setRunning(true);onBusy(true);
    try{let current=batch;while(!stop.current&&mounted.current&&context.current===own&&current.jobs.some(j=>j.status==="queued")){
      const r=await invokeCore<GenerationBatch>("generation_batch_next","generation.batch",{characterTarget:frozenTarget,batchId:batch.id});
      if(!r.payload)throw new Error(r.error?.message??"批量生成失败");
      current=r.payload;if(mounted.current&&context.current===own)put(current);
      if(current.jobs.some(j=>j.status==="uncertain"))break;
    }}catch(e){if(mounted.current)onError(e instanceof Error?e.message:String(e));}
    finally{if(mounted.current)setRunning(false);onBusy(false);}
  };
  const update=async(batch:GenerationBatch,job:BatchJob,action:string,reason="",promptParts?:PromptParts)=>{
    if(!target||running)return;const r=await invokeCore<GenerationBatch>("generation_batch_update","generation.batch",{characterTarget:target,batchId:batch.id,jobId:job.input.id,action,reason,promptParts});
    if(!r.payload){onError(r.error?.message??"更新失败");return;}put(r.payload);
  };
  return {batches,activeId,setActiveId,current:batches.find(b=>b.id===activeId)??batches[0]??null,running,refresh,create,run,update,pause:()=>{stop.current=true;}};
}
