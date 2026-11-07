import {describe,it,expect} from "vitest";
import {planGeneration,mergeReferences,type GenerationCatalog,type VariantResource,type SelectionSpec} from "./generation-catalog";
import {EMPTY_PROMPT} from "./cloud-generation";
const ref=(path:string)=>({path,purpose:path});
const variant:VariantResource={id:"original",name:"原皮",identity:"fixed face",outfit:"fixed clothes",quality:"anatomy",references:[ref("face.png"),ref("body.png")],issues:[],version:"v1"};
const catalog:GenerationCatalog={root:"root",characters:[],issues:[],poses:["a","b"].map(id=>({id,name:id,category:"通用",subcategory:"站姿",prompt_zh:id,prompt_en:`pose ${id}`,reference:ref(`${id}.png`),required_supports:["floor"],status:"accepted",version:"p1"})),scenes:["x","y"].map(id=>({id,name:id,category:"室内",group:"客厅",prompt:`scene ${id}`,capabilities:["floor"],views:[{id:"F",direction:"N",camera:"front",reference:ref(`${id}.png`)}],status:"design_reference",version:"s1"}))};
const spec:SelectionSpec={poses:["a","b"],scenes:["x","y"],randomPose:false,randomScene:false,count:3,mode:"total",direction:"F"};
describe("generation planning",()=>{
  it("expands total, per-pose and compatible-combinations distinctly",()=>{
    expect(planGeneration(catalog,variant,spec,EMPTY_PROMPT,[])).toHaveLength(3);
    expect(planGeneration(catalog,variant,{...spec,mode:"per_pose"},EMPTY_PROMPT,[])).toHaveLength(6);
    expect(planGeneration(catalog,variant,{...spec,mode:"combinations"},EMPTY_PROMPT,[])).toHaveLength(12);
  });
  it("fills each job with its own pose, direction and automatic reference roles",()=>{
    const jobs=planGeneration(catalog,variant,spec,EMPTY_PROMPT,[]);
    expect(jobs.map(j=>j.prompt_parts.pose)).toEqual(["pose a","pose b","pose a"]);
    expect(jobs.map(j=>j.scene_id)).toEqual(["x","x","y"]);
    expect(jobs[0].prompt_parts.identity).toContain("fixed clothes");
    expect(jobs[0].references.map(r=>r.source)).toEqual(["character","character","pose","scene"]);
    expect(jobs[0].prompt_parts.scene).toContain("相机朝向：N");
  });
  it("uses a reproducible injected random stream when preparing a batch",()=>{
    const jobs=planGeneration(catalog,variant,{...spec,randomPose:true,randomScene:true},EMPTY_PROMPT,[],()=>.99);
    expect(jobs.every(j=>j.pose_id==="b"&&j.scene_id==="y")).toBe(true);
  });
  it("rejects missing supports, withdrawn references, stale IDs and oversized expansion before generation",()=>{
    expect(()=>planGeneration(catalog,variant,{...spec,poses:["removed"]},EMPTY_PROMPT,[])).toThrow("变化");
    expect(()=>planGeneration({...catalog,poses:[{...catalog.poses[0],reference:null}]},variant,{...spec,poses:["a"]},EMPTY_PROMPT,[])).toThrow("灰模");
    expect(()=>planGeneration({...catalog,poses:[{...catalog.poses[0],required_supports:["mirror"]}]},variant,{...spec,poses:["a"]},EMPTY_PROMPT,[])).toThrow("支撑");
    expect(()=>planGeneration(catalog,variant,{...spec,mode:"combinations",count:500},EMPTY_PROMPT,[])).toThrow("2000");
    expect(()=>planGeneration(catalog,variant,{...spec,direction:"missing"},EMPTY_PROMPT,[])).toThrow();
  });
  it("keeps explicit edits for a single selection and replaces stale automatic attachments",()=>{
    const jobs=planGeneration(catalog,variant,{...spec,poses:["a"],scenes:["x"]},{...EMPTY_PROMPT,pose:"my pose",scene:"my scene"},[{...ref("old.png"),source:"pose"},ref("manual.png")]);
    expect(jobs[0].prompt_parts.pose).toBe("my pose");expect(jobs[0].prompt_parts.scene).toBe("my scene");
    expect(jobs[0].references.map(r=>r.path)).not.toContain("old.png");expect(jobs[0].references.at(-1)?.path).toBe("manual.png");
    expect(mergeReferences([ref("C:\\FACE.PNG")],[ref("c:/face.png")])).toHaveLength(1);
  });
});
