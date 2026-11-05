import { getInputProps } from "remotion";

export const fontFamily = "Arial";
let fontLoadCount = 0;
export const getFontLoadCount = () => fontLoadCount;
export function loadFont() {
  fontLoadCount++;
  if (getInputProps().testFontFailure) throw new Error("Test font unavailable");
  return { waitUntilDone: () => Promise.resolve() };
}
