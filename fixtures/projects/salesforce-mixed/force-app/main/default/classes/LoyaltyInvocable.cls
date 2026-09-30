public with sharing class LoyaltyInvocable {

    // Negative: an invocable action with a bulk query in user mode.
    @InvocableMethod(label='Recalculate loyalty tiers')
    public static void recalculate(List<Id> memberIds) {
        List<Loyalty_Member__c> members = [
            SELECT Id, Points__c FROM Loyalty_Member__c WHERE Id IN :memberIds WITH USER_MODE
        ];
        System.debug(LoggingLevel.FINE, members.size());
    }
}
