import { NextRequest, NextResponse } from 'next/server';
import { cookieOptions, origin, SESSION_COOKIE, signOut, validMutation } from '@mdp/showcase-auth';
import { session } from '../../server/session';
import { smallForm } from '../../server/form';
import { controlStore } from '../../server/clients';
export async function POST(request: NextRequest) {
  const current = await session();
  if (!current) return NextResponse.redirect(new URL('/sign-in?reason=ended', origin()), 303);
  const form = await smallForm(request).catch(() => null);
  if (!validMutation(current, request.headers.get('origin'), String(form?.get('csrf') ?? '')))
    return new Response('This request could not be checked. Open /today and try again.', { status: 403 });
  await signOut(controlStore(), current);
  const response = NextResponse.redirect(new URL('/sign-in?reason=ended', origin()), 303);
  response.cookies.set(SESSION_COOKIE, '', { ...cookieOptions, maxAge: 0 });
  response.headers.set('Clear-Site-Data', '"cache", "storage"');
  return response;
}
