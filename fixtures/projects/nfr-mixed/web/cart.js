export function load(raw) {
  try {
    return JSON.parse(raw);
  } catch (e) {}
}
export const run = (code) => eval(code);
