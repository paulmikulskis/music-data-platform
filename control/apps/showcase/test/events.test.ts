import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks=vi.hoisted(()=>({validate:vi.fn(),stop:vi.fn()}));
vi.mock('next/headers',()=>({cookies:async()=>({get:()=>({value:'fixture-token'})})}));
vi.mock('../server/session',()=>({session:()=>mocks.validate(),sessionForToken:(token:string)=>mocks.validate(token)}));
vi.mock('../server/heartbeat',()=>({heartbeat:{subscribe:(listener:{send:(event:null,online:boolean,runner:unknown)=>void})=>{listener.send(null,true,{state:'idle',next_scheduled_at:'2026-09-25T20:00:00.000Z'});return mocks.stop;}}}));
import {GET} from '../app/events/route';
beforeEach(()=>{vi.useFakeTimers();vi.clearAllMocks();});afterEach(()=>vi.useRealTimers());
it('revalidates an open stream after one minute and emits expiry before closing',async()=>{
 mocks.validate.mockResolvedValueOnce({person:{handle:'fixture'}}).mockResolvedValueOnce(null);
 const response=await GET(new Request('http://localhost/events'));const reader=response.body!.getReader();
 const connection=new TextDecoder().decode((await reader.read()).value);expect(connection).toContain('connection');expect(connection).toContain('2026-09-25T20:00:00.000Z');
 await vi.advanceTimersByTimeAsync(60000);
 expect(mocks.validate).toHaveBeenCalledTimes(2);expect(new TextDecoder().decode((await reader.read()).value)).toContain('expired');expect((await reader.read()).done).toBe(true);expect(mocks.stop).toHaveBeenCalledOnce();
});
it('unsubscribes and clears the timer when a hidden tab cancels its stream',async()=>{
 mocks.validate.mockResolvedValue({person:{handle:'fixture'}});const response=await GET(new Request('http://localhost/events'));await response.body!.cancel();await vi.advanceTimersByTimeAsync(120000);expect(mocks.validate).toHaveBeenCalledOnce();expect(mocks.stop).toHaveBeenCalledOnce();
});
