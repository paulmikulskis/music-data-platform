import { session } from '../../../../server/session';
import { consoleOperations, operationPending } from '../../../../server/console-operations';
import { payloadResponse } from '../../../../server/console-proxy';
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const current = await session();
  if (!current) return new Response('Open /sign-in.', { status: 401 });
  const { id } = await params;
  const operation = consoleOperations.get(id, current.id_hash);
  if (!operation) return new Response('This request is no longer available. Open /status.', { status: 404 });
  if (operation.result) return payloadResponse(operation.result);
  if (operation.failed) return new Response('The request lost its connection. Check progress at /status before starting more work.', { status: 503 });
  return operationPending(id);
}
