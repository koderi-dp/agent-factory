import { defaultUrlTransform } from "react-markdown";
export function safeUrl(url: string) {
  if (/^\s*(javascript|data):/i.test(url)) return "";
  return defaultUrlTransform(url);
}
