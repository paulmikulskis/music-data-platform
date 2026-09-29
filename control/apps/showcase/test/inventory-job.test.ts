import { afterEach, expect, it, vi } from 'vitest';
vi.mock('server-only',()=>({}));
import { nextInventoryTime, scheduleInventory } from '../server/inventory-snapshot';
afterEach(()=>{vi.useRealTimers();vi.unstubAllEnvs();vi.unstubAllGlobals();});
it('schedules at 00:30 UTC and never fills an earlier day',async()=>{
 expect(nextInventoryTime(new Date('2026-09-25T00:29:00Z')).toISOString()).toBe('2026-09-25T00:30:00.000Z');
 expect(nextInventoryTime(new Date('2026-09-25T00:30:00Z')).toISOString()).toBe('2026-09-26T00:30:00.000Z');
 vi.useFakeTimers();vi.setSystemTime(new Date('2026-09-25T00:29:59Z'));
 const task=vi.fn().mockResolvedValueOnce('deferred').mockResolvedValue('captured');const stop=scheduleInventory(task);
 await vi.advanceTimersByTimeAsync(1000);expect(task).toHaveBeenCalledTimes(1);
 await vi.advanceTimersByTimeAsync(60000);expect(task).toHaveBeenCalledTimes(2);
 await vi.advanceTimersByTimeAsync(23*3600000);expect(task).toHaveBeenCalledTimes(2);stop();
});
it('does not manufacture a missing day after a suspended process resumes',async()=>{
 vi.useFakeTimers();vi.setSystemTime(new Date('2026-09-25T00:29:59Z'));const task=vi.fn().mockResolvedValue('captured');const stop=scheduleInventory(task);
 vi.setSystemTime(new Date('2026-09-26T10:00:00Z'));await vi.advanceTimersByTimeAsync(1000);expect(task).not.toHaveBeenCalled();stop();
});

it('reports failures only through the authenticated functions endpoint',async()=>{
 const {sendInventoryAlert}=await import('../server/inventory-job');
 vi.stubEnv('MDP_SERVICE_URL','http://functions.invalid');vi.stubEnv('MDP_SERVICE_TOKEN','test-token');
 const warehouse='00000000-0000-4000-8000-000000000001';
 const request=vi.fn().mockResolvedValue(Response.json({warehouse}));vi.stubGlobal('fetch',request);
 expect(await sendInventoryAlert(warehouse)).toBe(warehouse);
 expect(request.mock.calls[0][0].pathname).toBe('/v1/alerts/showcase_inventory_failed');
 expect(request.mock.calls[0][1]).toMatchObject({method:'POST',headers:{Authorization:'Bearer test-token'},body:JSON.stringify({warehouse})});
 request.mockResolvedValue(new Response('',{status:503}));
 await expect(sendInventoryAlert(warehouse)).rejects.toThrow('/runbooks/showcase-inventory-failed');
});
