trigger AccountTrigger on Account (before insert, before update) {
    // Handler pattern: the trigger only delegates.
    AccountTriggerHandler.run(Trigger.new);
}
