import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { AuthPanel } from '../src/components/auth/AuthPanel';

describe('AuthPanel', () => {
  it('validates the email before calling the server', async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    render(
      <AuthPanel mode="login" onModeChange={() => {}} onSubmit={onSubmit} error={null} busy={false} />,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'not-an-email' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'whatever' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/valid email/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('enforces the minimum password length when signing up', async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    render(
      <AuthPanel mode="signup" onModeChange={() => {}} onSubmit={onSubmit} error={null} busy={false} />,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@example.org' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/at least 8 characters/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits valid credentials and shows the server error verbatim', async () => {
    const onSubmit = vi.fn().mockResolvedValue(false);
    const { rerender } = render(
      <AuthPanel mode="login" onModeChange={() => {}} onSubmit={onSubmit} error={null} busy={false} />,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.org' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a long password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(onSubmit).toHaveBeenCalledWith({
      email: 'person@example.org',
      password: 'a long password',
    });

    rerender(
      <AuthPanel
        mode="login"
        onModeChange={() => {}}
        onSubmit={onSubmit}
        error="Email or password is incorrect."
        busy={false}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Email or password is incorrect.');
  });

  it('disables the submit button while a request is in flight', () => {
    render(
      <AuthPanel
        mode="login"
        onModeChange={() => {}}
        onSubmit={vi.fn()}
        error={null}
        busy
      />,
    );

    expect(screen.getByRole('button', { name: /Signing in/ })).toBeDisabled();
  });

  it('switches between sign in and sign up', () => {
    const onModeChange = vi.fn();
    render(
      <AuthPanel mode="login" onModeChange={onModeChange} onSubmit={vi.fn()} error={null} busy={false} />,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Sign up' }));
    expect(onModeChange).toHaveBeenCalledWith('signup');
  });
});
