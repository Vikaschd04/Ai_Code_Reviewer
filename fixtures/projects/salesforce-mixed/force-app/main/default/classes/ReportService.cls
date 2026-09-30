public with sharing class ReportService {

    // Positive (unbounded query): no WHERE clause and no LIMIT.
    public static Integer countAccounts() {
        return [SELECT Id FROM Account WITH USER_MODE].size();
    }

    // Positive (high cost in loop): a global describe for every element.
    public static List<String> labels(List<String> objectNames) {
        List<String> result = new List<String>();
        for (String name : objectNames) {
            result.add(Schema.getGlobalDescribe().get(name).getDescribe().getLabel());
        }
        return result;
    }

    // Negative: one describe outside the loop.
    public static List<String> safeLabels(List<String> objectNames) {
        Map<String, Schema.SObjectType> types = Schema.getGlobalDescribe();
        List<String> result = new List<String>();
        for (String name : objectNames) {
            result.add(types.get(name).getDescribe().getLabel());
        }
        return result;
    }
}
