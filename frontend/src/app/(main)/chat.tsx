/**
 * AI Chat screen — conversational interface with the blood donation assistant.
 *
 * History is loaded from the server on mount, so a conversation survives closing
 * the app and is scoped to the authenticated account.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  FlatList,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { classifyError } from '@/api/errors';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { sendChatMessage, getChatHistory } from '@/api/chat';

interface LocalMessage {
  id: string;
  role: string;
  content: string;
}

const WELCOME: LocalMessage = {
  id: 'welcome',
  role: 'assistant',
  content:
    "Hello! I'm your Blood Donation Assistant. 🩸\n\nI can help you find donors, create blood requests, check eligibility, and more.\n\nHow can I help you today?",
};

const QUICK_REPLIES = [
  'Find O+ donors near me',
  'Am I eligible to donate?',
  'Show my donation history',
  'Create emergency request',
  'Nearby blood requests',
];

/** Must match the tab bar `height` in _layout.tsx so the offset is correct. */
const TAB_BAR_HEIGHT = 65;

/** Explain a failed send in terms the user can act on. */
function describeError(err: unknown): string {
  const failure = classifyError(err);
  // The assistant speaks in the first person, so the two failures it hits most
  // are phrased in its own voice rather than the app-wide wording.
  if (failure.kind === 'RATE_LIMITED') {
    return "You're sending messages faster than I can answer. Give me a moment and try again.";
  }
  if (failure.kind === 'UNKNOWN_ERROR') {
    return 'Sorry, I encountered an error. Please try again.';
  }
  return failure.message;
}

export default function ChatScreen() {
  const colors = useThemeColors();
  const [messages, setMessages] = useState<LocalMessage[]>([WELCOME]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const flatListRef = useRef<FlatList>(null);

  /**
   * Load the stored conversation. The endpoint returns only the caller's own
   * history — it is keyed off the authenticated user, not a client-supplied id.
   */
  const loadHistory = useCallback(async () => {
    try {
      const { messages: history } = await getChatHistory();
      if (history.length > 0) {
        setMessages(
          history.map((m, index) => ({
            id: `history-${index}`,
            role: m.role,
            content: m.content,
          }))
        );
      }
    } catch (e) {
      // A missing history is not worth an error screen — the welcome message
      // stands in and the user can still send.
      console.warn('Could not load chat history:', e);
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const handleSend = async (text?: string) => {
    const message = text || input.trim();
    if (!message || sending) return;

    const userMsg: LocalMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: message,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setSending(true);

    try {
      const response = await sendChatMessage(message);
      setMessages((prev) => [
        ...prev,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: response.content,
        },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: 'assistant',
          content: describeError(e),
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    setTimeout(() => {
      flatListRef.current?.scrollToEnd({ animated: true });
    }, 100);
  }, [messages]);

  const renderMessage = ({ item }: { item: LocalMessage }) => {
    const isUser = item.role === 'user';
    return (
      <View
        style={[
          styles.messageBubble,
          isUser
            ? [styles.userBubble, { backgroundColor: colors.primary }]
            : [styles.assistantBubble, { backgroundColor: colors.surface, borderColor: colors.border }],
        ]}
      >
        {!isUser && <Text style={styles.assistantIcon}>🤖</Text>}
        <Text
          style={[
            styles.messageText,
            { color: isUser ? colors.textOnPrimary : colors.text },
          ]}
        >
          {item.content}
        </Text>
      </View>
    );
  };

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: colors.background }}
      behavior="padding"
      keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : TAB_BAR_HEIGHT}
    >
      {/* Header */}
      <View style={[styles.header, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
        <Text style={[styles.headerTitle, { color: colors.text }]}>🤖 AI Assistant</Text>
        <Text style={[styles.headerSubtitle, { color: colors.textSecondary }]}>
          Blood Donation Helper
        </Text>
      </View>

      {/* Messages */}
      {loadingHistory ? (
        <View style={styles.historyLoading}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <FlatList
          ref={flatListRef}
          style={styles.flatList}
          data={messages}
          keyExtractor={(item) => item.id}
          renderItem={renderMessage}
          contentContainerStyle={styles.messageList}
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
        />
      )}

      {/* Quick Replies */}
      {!loadingHistory && messages.length <= 1 && (
        <View style={styles.quickReplies}>
          {QUICK_REPLIES.map((reply) => (
            <Pressable
              key={reply}
              style={[styles.quickReply, { backgroundColor: colors.surfaceVariant, borderColor: colors.border }]}
              onPress={() => handleSend(reply)}
            >
              <Text style={[styles.quickReplyText, { color: colors.primary }]}>{reply}</Text>
            </Pressable>
          ))}
        </View>
      )}

      {/* Typing indicator */}
      {sending && (
        <View style={[styles.typingIndicator, { backgroundColor: colors.surface }]}>
          <ActivityIndicator size="small" color={colors.primary} />
          <Text style={[styles.typingText, { color: colors.textSecondary }]}>Thinking...</Text>
        </View>
      )}

      {/* Input */}
      <View style={[styles.inputContainer, { backgroundColor: colors.surface, borderTopColor: colors.border }]}>
        <TextInput
          style={[styles.textInput, { backgroundColor: colors.surfaceVariant, color: colors.text }]}
          placeholder="Ask about blood donation..."
          placeholderTextColor={colors.textTertiary}
          value={input}
          onChangeText={setInput}
          onSubmitEditing={() => handleSend()}
          multiline
          maxLength={500}
          editable={!sending}
        />
        <Pressable
          style={[
            styles.sendButton,
            { backgroundColor: input.trim() && !sending ? colors.primary : colors.surfaceVariant },
          ]}
          onPress={() => handleSend()}
          disabled={!input.trim() || sending}
        >
          <Text style={[styles.sendIcon, { color: input.trim() && !sending ? colors.textOnPrimary : colors.textTertiary }]}>
            ➤
          </Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: Spacing.xl,
    paddingTop: 60,
    paddingBottom: Spacing.base,
    borderBottomWidth: 1,
  },
  headerTitle: {
    fontSize: Typography.sizes.lg,
    fontWeight: '800',
  },
  headerSubtitle: {
    fontSize: Typography.sizes.xs,
    marginTop: 2,
  },
  messageList: {
    padding: Spacing.base,
    paddingBottom: Spacing.xl,
    gap: Spacing.sm,
  },
  flatList: {
    flex: 1,
  },
  historyLoading: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  messageBubble: {
    maxWidth: '85%',
    padding: Spacing.md,
    borderRadius: Radius.lg,
  },
  userBubble: {
    alignSelf: 'flex-end',
    borderBottomRightRadius: Radius.xs,
  },
  assistantBubble: {
    alignSelf: 'flex-start',
    borderBottomLeftRadius: Radius.xs,
    borderWidth: 1,
  },
  assistantIcon: {
    fontSize: 16,
    marginBottom: Spacing.xs,
  },
  messageText: {
    fontSize: Typography.sizes.sm,
    lineHeight: 21,
  },
  quickReplies: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    padding: Spacing.base,
    gap: Spacing.sm,
  },
  quickReply: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderRadius: Radius.full,
    borderWidth: 1,
  },
  quickReplyText: {
    fontSize: Typography.sizes.xs,
    fontWeight: '600',
  },
  typingIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.sm,
  },
  typingText: {
    fontSize: Typography.sizes.xs,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    padding: Spacing.md,
    borderTopWidth: 1,
    gap: Spacing.sm,
    paddingBottom: Platform.OS === 'ios' ? 30 : Spacing.md,
  },
  textInput: {
    flex: 1,
    borderRadius: Radius.xl,
    paddingHorizontal: Spacing.base,
    paddingVertical: 10,
    fontSize: Typography.sizes.sm,
    maxHeight: 100,
  },
  sendButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sendIcon: {
    fontSize: 18,
    fontWeight: '700',
  },
});
