# user_bot/states.py
# ConversationHandler states

(
    # Login flow — simplified (owner API used, no API ID/Hash from user)
    STATE_LOGIN_API_ID,      # kept for range() compat — not used in new flow
    STATE_LOGIN_API_HASH,    # kept for range() compat — not used in new flow
    STATE_LOGIN_PHONE,
    STATE_LOGIN_OTP,
    STATE_LOGIN_2FA,

    # Master Ad Conversation (single unified conv)
    STATE_AD_PICK_ACCOUNTS,
    STATE_AD_PICK_ACCOUNT,
    STATE_AD_PICK_MODE,
    STATE_AD_FILE_TYPE,
    STATE_AD_CUSTOM_CONTENT,
    STATE_AD_POST_LINK,
    STATE_AD_GROUP_TARGET,
    STATE_AD_SELECT_GROUPS,
    STATE_AD_TOPIC_INPUT,
    STATE_AD_GROUP_DELAY,
    STATE_AD_PROCESS_DELAY,

    # Settings
    STATE_SET_PICK_ACCOUNT,
    STATE_SET_MENU,
    STATE_SET_GROUP_DELAY,
    STATE_SET_PROCESS_DELAY,
    STATE_SET_TRACK_CHANNEL,

    # Forum Topic Finder
    STATE_FORUM_PICK_ACCOUNT,
    STATE_FORUM_SEARCH_KEYWORD,

    # Ad Template — save name
    STATE_TEMPLATE_SAVE_NAME,

    # Profile Update conversation
    STATE_PROF_UPDATE_NAME,
    STATE_PROF_UPDATE_BIO,
    STATE_PROF_UPDATE_PHOTO,

) = range(27)
