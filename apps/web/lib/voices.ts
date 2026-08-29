import { authorizedJson } from "./auth";

export interface Voice {
  id: string;
  name: string;
  language: string;
  gender: string | null;
  preview_url: string | null;
}

export async function listVoices(): Promise<Voice[]> {
  return authorizedJson<Voice[]>("/api/v1/voices");
}
