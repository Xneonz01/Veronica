// A toast from anywhere in the window: the outcome of an action taken away from
// where it shows, or a failure with a way to retry. Memory writes have their own
// source (the notification provider) and share the same stack.

import { createContext, useContext } from 'react'

export type ToastTone = 'info' | 'positive' | 'critical'

export interface ToastInput {
  tone?: ToastTone
  title: string
  body?: string
  action?: { label: string; run: () => void }
}

export interface Toaster {
  show: (toast: ToastInput) => void
}

export const ToastContext = createContext<Toaster | null>(null)

export function useToasts(): Toaster {
  const toaster = useContext(ToastContext)
  if (!toaster) throw new Error('useToasts needs the ToastProvider (in the app shell)')
  return toaster
}
