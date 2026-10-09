import { View, Text, StyleSheet } from 'react-native';
import { useThemeColors, Typography, Spacing, Radius } from '@/theme';
import { ChatMessage } from '@/api/chat';

export function ChatBubble({ message }: { message: ChatMessage }) {
  const colors = useThemeColors();
  const isUser = message.role === 'user';

  return (
    <View style={[styles.container, isUser ? styles.userContainer : styles.botContainer]}>
      <View
        style={[
          styles.bubble,
          {
            backgroundColor: isUser ? colors.primary : colors.surfaceVariant,
            borderBottomRightRadius: isUser ? 0 : Radius.lg,
            borderBottomLeftRadius: isUser ? Radius.lg : 0,
          },
        ]}
      >
        <Text style={[styles.text, { color: isUser ? colors.textOnPrimary : colors.text }]}>
          {message.content}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: Spacing.xs,
    flexDirection: 'row',
  },
  userContainer: {
    justifyContent: 'flex-end',
    marginLeft: 40,
  },
  botContainer: {
    justifyContent: 'flex-start',
    marginRight: 40,
  },
  bubble: {
    padding: Spacing.md,
    borderRadius: Radius.lg,
  },
  text: {
    fontSize: Typography.sizes.base,
    lineHeight: 22,
  },
});
