import { z } from "zod";
import { Ajv2020 } from "ajv/dist/2020.js";
import { fullFormats } from "ajv-formats/dist/formats.js";
export const object = z.record(z.string(), z.unknown());
export const document = z.object({paths:z.record(z.string(),z.record(z.string(),object)),components:z.object({schemas:z.record(z.string(),object).default({})}).default({schemas:{}})});
export type Document = z.infer<typeof document>;
export function resolve(value: unknown, doc: Document): Record<string, unknown> {
  const node = object.parse(value);
  if (typeof node.$ref === "string") {
    const name = node.$ref.replace("#/components/schemas/", "");
    const target = doc.components.schemas[name];
    if (!target) throw new Error(`Unresolved schema ${name}`);
    return resolve(target, doc);
  }
  return node;
}
export function validator(schema: unknown, doc: Document) {
  const ajv = new Ajv2020({strict:false,allErrors:true});
  for (const [name,format] of Object.entries(fullFormats)) ajv.addFormat(name,format);
  return ajv.compile({...object.parse(schema),components:doc.components});
}
// Direction matters: every value promised by the producer must be consumable.
// Compare consumed fields recursively; producer-only fields may be ignored by Zod.
export function compatible(producer: unknown, consumer: unknown, doc: Document, at="response"): void {
  const p = resolve(producer,doc), c = object.parse(consumer);
  const variants = (n: Record<string,unknown>) => Array.isArray(n.anyOf) ? n.anyOf : Array.isArray(n.oneOf) ? n.oneOf : Array.isArray(n.type) ? n.type.map(type=>({...n,type})) : [n];
  if (variants(p).length > 1 || variants(c).length > 1) {
    for (const pv of variants(p)) {
      let found=false;
      for (const cv of variants(c)) { try { compatible(pv,cv,doc,at);found=true;break; } catch {} }
      if (!found) throw new Error(`${at}: incompatible union/nullability`);
    }
    return;
  }
  if (c.type && p.type !== c.type) throw new Error(`${at}: expected ${c.type}, service promises ${p.type}`);
  const allowed = c.enum;
  if (Array.isArray(allowed) && (!Array.isArray(p.enum) || p.enum.some(v=>!allowed.includes(v)))) throw new Error(`${at}: enum is broader than consumer`);
  if (c.type === "object") {
    const properties=object.parse(p.properties ?? {}), consumed=object.parse(c.properties ?? {});
    const required=z.array(z.string()).parse(p.required ?? []);
    for (const key of z.array(z.string()).parse(c.required ?? [])) if (!required.includes(key)) throw new Error(`${at}.${key}: not required by service`);
    for (const [key,child] of Object.entries(consumed)) {
      if (!(key in properties)) { if (z.array(z.string()).parse(c.required ?? []).includes(key)) throw new Error(`${at}.${key}: service field absent`); continue; }
      compatible(properties[key],child,doc,`${at}.${key}`);
    }
  }
  if (c.type === "array") compatible(p.items,c.items,doc,`${at}[]`);
}
