public with sharing class LoyaltyQueueable implements Queueable {

    // Positive (async): no Finalizer is attached, so failures go unnoticed.
    public void execute(QueueableContext context) {
        List<Loyalty_Member__c> members = [SELECT Id, Points__c FROM Loyalty_Member__c WITH USER_MODE LIMIT 200];
        for (Loyalty_Member__c member : members) {
            member.Points__c = member.Points__c + 1;
        }
        Database.update(members, AccessLevel.USER_MODE);
    }
}
