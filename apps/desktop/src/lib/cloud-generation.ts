export type ApiProfile = {
  id: string; name: string; base_url: string; model: string;
  protocol: "images" | "grok"; has_key: boolean;
};
export type SubscriptionAccount = {
  available: boolean; logged_in: boolean; name: string; plan: string;
  home: string; config_exists: boolean; auth_file_exists: boolean; message: string;
};
export type GenerationReference = { path: string; purpose: string; source?: "character" | "pose" | "scene"; resource_id?: string };
export type CloudResult = { path: string; output_dir: string; status: string };
export const EMPTY_PROFILE = { id: "", name: "", base_url: "https://api.openai.com/v1", model: "gpt-image-2", protocol: "images", key: "" };
export const PROMPT_PARTS = [
  ["identity", "角色与衣装"], ["pose", "姿势动作"], ["expression", "表情"],
  ["scene", "场景"], ["camera", "镜头与构图"], ["quality", "质量约束"],
] as const;
export type PromptKey = typeof PROMPT_PARTS[number][0];
export type PromptParts = Record<PromptKey, string>;
export const EMPTY_PROMPT: PromptParts = { identity: "", pose: "", expression: "", scene: "", camera: "", quality: "" };
export function composePrompt(parts: PromptParts, references: GenerationReference[]): string {
  const text = PROMPT_PARTS.map(([key]) => parts[key].trim()).filter(Boolean).join("\n\n");
  return text + references.map((reference, index) =>
    reference.purpose.trim() ? "\nReference image " + (index + 1) + ": " + reference.purpose.trim() : "",
  ).join("");
}
