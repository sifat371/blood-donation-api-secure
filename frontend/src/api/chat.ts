/**
 * Chat API functions.
 */

import api from './client';

export interface ChatMessage {
  role: string;
  content: string;
  tool_name?: string;
  tool_result?: string;
}

export async function sendChatMessage(message: string): Promise<ChatMessage> {
  const { data } = await api.post<ChatMessage>('/chat/message', { message });
  return data;
}

export async function getChatHistory(): Promise<{ messages: ChatMessage[] }> {
  const { data } = await api.get<{ messages: ChatMessage[] }>('/chat/history');
  return data;
}
