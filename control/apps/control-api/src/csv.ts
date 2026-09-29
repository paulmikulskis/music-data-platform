import { parse } from "csv-parse/sync";
import { z } from "zod";
import { AppError } from "./db.js";
const csvRow = z
  .object({
    platform: z.string().min(1),
    platform_account_id: z.string().optional(),
    handle: z.string().optional(),
    display_name: z.string().optional(),
    role: z.string().optional(),
  })
  .strict()
  .refine(
    (r) => Boolean(r.platform_account_id || r.handle),
    "Supply handle or platform_account_id",
  );
export function inspectTargetCsv(text:string) {
  const rows: z.infer<typeof csvRow>[] = [];
  const errors:string[]=[];
  const identities=new Set<string>();
  try {
    const records: {record:unknown;info:{lines:number;error?:unknown}}[] = parse(text,{columns:true,skip_empty_lines:true,trim:true,bom:true,info:true,relax_column_count:true});
    for (const item of records) {
      if(item.info.error){errors.push(`Line ${item.info.lines}: Column count does not match the header.`);continue;}
      const parsed=csvRow.safeParse(item.record);
      if (!parsed.success) {errors.push(`Line ${item.info.lines}: Expected platform and handle or platform_account_id; optional display_name and role.`);continue;}
      const key=`${parsed.data.platform}:${parsed.data.platform_account_id || parsed.data.handle}`;
      if (identities.has(key)) {errors.push(`Line ${item.info.lines}: Duplicate identity.`);continue;}
      identities.add(key); rows.push(parsed.data);
    }
    if (!records.length) errors.push('Line 2: Supply at least one target below the header.');
    if (records.length>1000) errors.push('Line 1002: At most 1000 targets can be imported at once.');
  } catch (error) {
    const line = error && typeof error==='object' && 'lines' in error ? String(error.lines) : '1';
    errors.push(`Line ${line}: Invalid CSV quoting or columns. Expected platform, handle or platform_account_id.`);
  }
  return {rows:rows.slice(0,1000),errors};
}
export function targetCsv(text: string) {
  const preview=inspectTargetCsv(text);
  if(preview.errors.length) throw new AppError('invalid_csv',preview.errors.join(' '),422);
  return preview.rows;
}
